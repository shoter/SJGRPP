/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file headless_v.h Video driver which renders the full GUI into an off-screen buffer, without any OS window. */

#ifndef VIDEO_HEADLESS_H
#define VIDEO_HEADLESS_H

#include "video_driver.hpp"

#include <vector>

/**
 * Headless video driver.
 * Behaves like a normal GUI client (windows, viewports, input handling and drawing all run),
 * but the screen is an in-memory buffer which is never presented. It is intended for automated
 * testing and debugging: screenshots of the buffer can be taken, and input can be injected,
 * via the debug bridge (see debug_bridge.h).
 *
 * Driver parameters (-v headless:param1,param2=value):
 *  - lockstep     start in lockstep mode: game ticks only advance when requested via the debug bridge
 */
class VideoDriver_Headless : public VideoDriver {
private:
	std::vector<uint8_t> buffer; ///< Off-screen video buffer.
	Palette local_palette{};     ///< Current palette, as seen by the "display".

	void AllocateBackingStore(int w, int h);
	void RunLockstepTicks();

protected:
	void CheckPaletteAnim() override;
	void Paint() override;

public:
	const char *Start(const StringList &param) override;
	void Stop() override;
	void MakeDirty(int left, int top, int width, int height) override;
	void MainLoop() override;
	bool ChangeResolution(int w, int h) override;
	bool ToggleFullscreen(bool fullscreen) override;
	bool AfterBlitterChange() override;
	Dimension GetScreenSize() const override { return { 1920, 1080 }; }
	const char *GetName() const override { return "headless"; }
	bool HasGUI() const override { return true; }
};

/** Factory for the headless video driver. */
class FVideoDriver_Headless : public DriverFactoryBase {
public:
	FVideoDriver_Headless() : DriverFactoryBase(Driver::Type::Video, 0, "headless", "Headless Video Driver (off-screen rendering, for automated testing)") {}
	std::unique_ptr<Driver> CreateInstance() const override { return std::make_unique<VideoDriver_Headless>(); }
};

#endif /* VIDEO_HEADLESS_H */
