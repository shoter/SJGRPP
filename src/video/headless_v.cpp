/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file headless_v.cpp Video driver which renders the full GUI into an off-screen buffer, without any OS window. */

#include "../stdafx.h"
#include "../gfx_func.h"
#include "../openttd.h"
#include "../progress.h"
#include "../blitter/factory.hpp"
#include "../core/math_func.hpp"
#include "../debug_bridge.h"
#include "../thread.h"
#include "../window_func.h"
#include "headless_v.h"

#include <atomic>

#include "../safeguards.h"

/** Factory for the headless video driver. */
static FVideoDriver_Headless iFVideoDriver_Headless;

extern std::atomic<bool> _exit_game;

const char *VideoDriver_Headless::Start(const StringList &parm)
{
#ifdef _MSC_VER
	/* Disable the MSVC assertion message box, nobody is there to click it. */
	_set_error_mode(_OUT_TO_STDERR);
	_CrtSetReportMode(_CRT_ASSERT, _CRTDBG_MODE_FILE);
	_CrtSetReportFile(_CRT_ASSERT, _CRTDBG_FILE_STDERR);
#endif

	this->UpdateAutoResolution();

	Blitter *blitter = BlitterFactory::GetCurrentBlitter();
	if (blitter == nullptr || blitter->GetScreenDepth() == 0) return "headless video driver requires a blitter with a non-zero screen depth";

	this->AllocateBackingStore(_cur_resolution.width, _cur_resolution.height);
	ScreenSizeChanged();
	MarkWholeScreenDirty();

	/* Everything runs on the main thread, this keeps the game deterministic with respect to bridge commands. */
	this->is_game_threaded = false;

	DebugBridge::SetLockstepSupported(true);
	if (GetDriverParamBool(parm, "lockstep")) DebugBridge::SetLockstep(true);

	return nullptr;
}

void VideoDriver_Headless::Stop()
{
	DebugBridge::SetLockstepSupported(false);
	this->buffer.clear();
	this->buffer.shrink_to_fit();
}

void VideoDriver_Headless::AllocateBackingStore(int w, int h)
{
	w = std::max(w, 64);
	h = std::max(h, 64);

	uint bpp = BlitterFactory::GetCurrentBlitter()->GetScreenDepth();
	uint pitch = (bpp == 8) ? Align(w, 4) : w;

	this->buffer.assign(static_cast<size_t>(pitch) * h * (bpp / 8), 0);

	_screen.width = w;
	_screen.pitch = pitch;
	_screen.height = h;
	_screen.dst_ptr = this->buffer.data();

	/* Mark all palette colours dirty. */
	_cur_palette.first_dirty = 0;
	_cur_palette.count_dirty = 256;
	this->local_palette = _cur_palette;

	BlitterFactory::GetCurrentBlitter()->PostResize();
}

void VideoDriver_Headless::MakeDirty(int, int, int, int) {}

void VideoDriver_Headless::CheckPaletteAnim()
{
	if (_cur_palette.count_dirty == 0) return;

	this->local_palette = _cur_palette;
	_cur_palette.count_dirty = 0;
}

void VideoDriver_Headless::Paint()
{
	if (this->local_palette.count_dirty == 0) return;

	/* There is no display, but a palette animating blitter still has to regenerate the screen buffer from its animation buffer. */
	Blitter *blitter = BlitterFactory::GetCurrentBlitter();
	if (blitter->UsePaletteAnimation() == Blitter::PaletteAnimation::Blitter) blitter->PaletteAnimate(this->local_palette);
	this->local_palette.count_dirty = 0;
}

/**
 * Run the game ticks requested via the debug bridge while in lockstep mode.
 * Bounded in wall-clock time so that drawing and bridge polling keep happening during long runs.
 */
void VideoDriver_Headless::RunLockstepTicks()
{
	const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(100);
	while (!_exit_game && DebugBridge::TakeLockstepTick()) {
		::GameLoop();
		if (std::chrono::steady_clock::now() > deadline) break;
	}
}

void VideoDriver_Headless::MainLoop()
{
	SetSelfAsGameThread();

	bool was_lockstep = false;
	while (!_exit_game) {
		/* Lockstep only holds a running game; mode switches, world generation and the intro game keep ticking. */
		const bool hold = DebugBridge::IsLockstep() && _switch_mode == SwitchMode::None && !HasModalProgress() &&
				(_game_mode == GameMode::Normal || _game_mode == GameMode::Editor);
		if (hold) {
			std::lock_guard<std::recursive_mutex> lock(this->game_state_mutex);
			this->RunLockstepTicks();

			/* Keep the regular game-tick scheduler from running ticks on its own. */
			this->next_game_tick = std::chrono::steady_clock::now() + std::chrono::hours(1);
			was_lockstep = true;
		} else if (was_lockstep) {
			this->next_game_tick = std::chrono::steady_clock::now();
			was_lockstep = false;
		}

		this->Tick();
		this->SleepTillNextTick();
	}
}

bool VideoDriver_Headless::ChangeResolution(int w, int h)
{
	this->AllocateBackingStore(w, h);
	GameSizeChanged();
	return true;
}

bool VideoDriver_Headless::ToggleFullscreen(bool) { return false; }

bool VideoDriver_Headless::AfterBlitterChange()
{
	if (BlitterFactory::GetCurrentBlitter()->GetScreenDepth() == 0) return false;
	this->AllocateBackingStore(_screen.width, _screen.height);
	return true;
}
