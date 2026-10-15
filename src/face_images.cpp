/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file face_images.cpp Company manager face images loaded from the faces/ folder. */

#include "stdafx.h"
#include "face_images.h"
#include "company_type.h"
#include "debug.h"
#include "fileio_func.h"
#include "gfx_func.h"
#include "palette_func.h"
#include "spritecache.h"
#include "string_func.h"
#include "blitter/factory.hpp"
#include "spriteloader/spriteloader.hpp"

#include <map>

#ifdef WITH_PNG
#include <png.h>
#endif /* WITH_PNG */

#include "safeguards.h"

static std::vector<FaceImageInfo> _face_images; ///< All face images, sorted by id.

/** Encoded face image sprites, keyed by id and size. A null pointer means the image could not be loaded. */
static std::map<std::tuple<std::string, uint, uint>, std::unique_ptr<std::byte[]>, std::less<>> _face_image_cache;
static const Blitter *_face_image_cache_blitter = nullptr; ///< Blitter the cached sprites were encoded for.

/**
 * Turn a file name word into a display string: underscores become spaces and words start with a capital.
 * @param word Word such as "donald_trump".
 * @return Display string such as "Donald Trump".
 */
std::string FormatFaceImageWord(std::string_view word)
{
	std::string result;
	result.reserve(word.size());
	bool word_start = true;
	for (char c : word) {
		if (c == '_' || c == '-' || c == ' ') {
			result += ' ';
			word_start = true;
			continue;
		}
		result += (word_start && c >= 'a' && c <= 'z') ? static_cast<char>(c - 'a' + 'A') : c;
		word_start = false;
	}
	return result;
}

/** Scanner for the PNG files in the faces/ folder. */
class FaceImageScanner : public FileScanner {
public:
	bool AddFile(const std::string &filename, size_t basepath_length, const std::string &) override
	{
		std::string_view relative = std::string_view{filename}.substr(basepath_length);
		/* Skip files in subfolders. */
		if (relative.find_first_of("/\\") != std::string_view::npos) return false;

		std::string_view id = relative.substr(0, relative.size() - 4); // strip ".png"
		if (id.empty() || id.size() > MAX_FACE_IMAGE_ID_LENGTH) return false;

		/* The same file may be found in more than one search path; the first one wins. */
		if (std::ranges::any_of(_face_images, [&](const FaceImageInfo &f) { return f.id == id; })) return false;

		FaceImageInfo &info = _face_images.emplace_back();
		info.id = id;
		info.path = filename;

		size_t first = id.find('_');
		size_t second = first == std::string_view::npos ? std::string_view::npos : id.find('_', first + 1);
		if (second == std::string_view::npos) {
			/* Not in the type_category_name form; keep it, but without type and category. */
			info.name = FormatFaceImageWord(id);
		} else {
			info.type = id.substr(0, first);
			info.category = id.substr(first + 1, second - first - 1);
			info.name = FormatFaceImageWord(id.substr(second + 1));
		}
		return true;
	}
};

/** Scan the faces/ folder(s) for face images. */
void ScanFaceImages()
{
	_face_images.clear();
	FaceImageScanner scanner;
	scanner.Scan(".png", Subdirectory::Faces, false, false);
	std::ranges::sort(_face_images, std::less{}, &FaceImageInfo::id);
	ClearFaceImageCache();
	Debug(misc, 1, "{} face images found", _face_images.size());
}

/**
 * Get all known face images.
 * @return Face images, sorted by id.
 */
const std::vector<FaceImageInfo> &GetFaceImages()
{
	return _face_images;
}

/**
 * Find a face image by id.
 * @param id Face image id.
 * @return The face image, or nullptr if there is no image with this id.
 */
const FaceImageInfo *FindFaceImage(std::string_view id)
{
	auto it = std::ranges::lower_bound(_face_images, id, std::less{}, &FaceImageInfo::id);
	if (it == _face_images.end() || it->id != id) return nullptr;
	return &*it;
}

/**
 * Get the image shown for a company that did not choose one.
 * @param seed Random value stored with the company face.
 * @return Face image, or nullptr if there are no face images at all.
 */
const FaceImageInfo *GetAutoFaceImage(uint32_t seed)
{
	if (_face_images.empty()) return nullptr;
	/* Mix the bits, as the seed is not uniformly distributed. */
	uint32_t h = seed;
	h ^= h >> 16;
	h *= 0x7FEB352D;
	h ^= h >> 15;
	h *= 0x846CA68B;
	h ^= h >> 16;
	return &_face_images[h % _face_images.size()];
}

/**
 * Get the face image shown for a company manager face.
 * @param cmf The company manager's face.
 * @return The chosen image, or the automatic one if none was chosen; nullptr if it is not available.
 */
const FaceImageInfo *GetCompanyFaceImage(const CompanyManagerFace &cmf)
{
	return cmf.image.empty() ? GetAutoFaceImage(cmf.bits) : FindFaceImage(cmf.image);
}

/**
 * Check whether the favourite face setting holds a face image id.
 * Older versions stored a face code (`label:bits`) there, which is ignored.
 * @param setting Value of the setting.
 * @return True if the setting holds a face image id.
 */
bool IsValidFaceImageSetting(std::string_view setting)
{
	return !setting.empty() && setting.size() <= MAX_FACE_IMAGE_ID_LENGTH && setting.find(':') == std::string_view::npos;
}

/** Forget all encoded face image sprites. */
void ClearFaceImageCache()
{
	_face_image_cache.clear();
	_face_image_cache_blitter = nullptr;
}

#ifdef WITH_PNG
/** RGBA image in memory. */
struct RgbaImage {
	uint width = 0;
	uint height = 0;
	std::vector<uint8_t> pixels; ///< 4 bytes per pixel, row by row.
};

/**
 * Read a PNG file into RGBA pixels.
 * @param path Full path of the file.
 * @return The image, or std::nullopt on error.
 */
static std::optional<RgbaImage> ReadFacePNG(const std::string &path)
{
	auto fp = FileHandle::Open(path, "rb");
	if (!fp.has_value()) return std::nullopt;

	png_structp png_ptr = png_create_read_struct(PNG_LIBPNG_VER_STRING, nullptr, nullptr, nullptr);
	if (png_ptr == nullptr) return std::nullopt;

	png_infop info_ptr = png_create_info_struct(png_ptr);
	if (info_ptr == nullptr || setjmp(png_jmpbuf(png_ptr))) {
		png_destroy_read_struct(&png_ptr, &info_ptr, nullptr);
		return std::nullopt;
	}

	png_init_io(png_ptr, *fp);
	png_read_png(png_ptr, info_ptr, PNG_TRANSFORM_EXPAND | PNG_TRANSFORM_STRIP_16 | PNG_TRANSFORM_GRAY_TO_RGB, nullptr);

	uint channels = png_get_channels(png_ptr, info_ptr);
	if (png_get_bit_depth(png_ptr, info_ptr) != 8 || (channels != 3 && channels != 4)) {
		png_destroy_read_struct(&png_ptr, &info_ptr, nullptr);
		return std::nullopt;
	}

	RgbaImage img;
	img.width = png_get_image_width(png_ptr, info_ptr);
	img.height = png_get_image_height(png_ptr, info_ptr);
	if (img.width == 0 || img.height == 0 || img.width > 4096 || img.height > 4096) {
		png_destroy_read_struct(&png_ptr, &info_ptr, nullptr);
		return std::nullopt;
	}

	img.pixels.resize(static_cast<size_t>(img.width) * img.height * 4);
	png_bytep *rows = png_get_rows(png_ptr, info_ptr);
	for (uint y = 0; y < img.height; y++) {
		uint8_t *dst = &img.pixels[static_cast<size_t>(y) * img.width * 4];
		for (uint x = 0; x < img.width; x++) {
			const uint8_t *src = rows[y] + x * channels;
			dst[0] = src[0];
			dst[1] = src[1];
			dst[2] = src[2];
			dst[3] = channels == 4 ? src[3] : 0xFF;
			dst += 4;
		}
	}

	png_destroy_read_struct(&png_ptr, &info_ptr, nullptr);
	return img;
}

/**
 * Load a face image, scale it to fill the given size and encode it for the current blitter.
 * The image is cropped to the box ratio: centred horizontally, top-aligned vertically (heads are at the top).
 * @param path Full path of the file.
 * @param width Target width.
 * @param height Target height.
 * @return Encoded sprite data, or nullptr on error.
 */
static std::unique_ptr<std::byte[]> LoadFaceSprite(const std::string &path, uint width, uint height)
{
	auto img = ReadFacePNG(path);
	if (!img.has_value()) {
		Debug(misc, 0, "Could not load face image {}", path);
		return nullptr;
	}

	/* Source area to use, with the same ratio as the target. */
	double src_w = img->width;
	double src_h = img->height;
	if (src_w * height > src_h * width) {
		src_w = src_h * width / height;
	} else {
		src_h = src_w * height / width;
	}
	double src_x = (img->width - src_w) / 2;
	double scale_x = src_w / width;
	double scale_y = src_h / height;

	Blitter *blitter = BlitterFactory::GetCurrentBlitter();
	bool palette_only = blitter->GetScreenDepth() == 8;

	SpriteLoader::SpriteCollection collection;
	SpriteLoader::Sprite &sprite = collection[ZoomLevel::Min];
	sprite.AllocateData(ZoomLevel::Min, static_cast<size_t>(width) * height);
	sprite.colours = palette_only ? SpriteComponents{SpriteComponent::Palette} : SpriteComponents{SpriteComponent::RGB, SpriteComponent::Alpha};
	sprite.width = width;
	sprite.height = height;
	sprite.x_offs = 0;
	sprite.y_offs = 0;

	/* Box filter: average all source pixels covered by each target pixel. */
	for (uint y = 0; y < height; y++) {
		uint sy0 = static_cast<uint>(y * scale_y);
		uint sy1 = std::clamp<uint>(static_cast<uint>((y + 1) * scale_y + 0.999), sy0 + 1, img->height);
		for (uint x = 0; x < width; x++) {
			uint sx0 = static_cast<uint>(src_x + x * scale_x);
			uint sx1 = std::clamp<uint>(static_cast<uint>(src_x + (x + 1) * scale_x + 0.999), sx0 + 1, img->width);

			uint32_t sum[4] = {};
			uint32_t count = 0;
			for (uint sy = sy0; sy < sy1; sy++) {
				const uint8_t *p = &img->pixels[(static_cast<size_t>(sy) * img->width + sx0) * 4];
				for (uint sx = sx0; sx < sx1; sx++) {
					sum[0] += p[0];
					sum[1] += p[1];
					sum[2] += p[2];
					sum[3] += p[3];
					p += 4;
					count++;
				}
			}

			SpriteLoader::CommonPixel &px = sprite.data[static_cast<size_t>(y) * width + x];
			px.r = sum[0] / count;
			px.g = sum[1] / count;
			px.b = sum[2] / count;
			px.a = sum[3] / count;
			px.m = 0;
			if (palette_only) {
				px.m = px.a < 128 ? 0 : GetNearestColourIndex(px.r, px.g, px.b);
			}
		}
	}

	UniquePtrSpriteAllocator allocator;
	blitter->Encode(SpriteType::Font, collection, allocator);
	return std::move(allocator.data);
}
#endif /* WITH_PNG */

/**
 * Draw a face image filling the given rectangle.
 * @param id Face image id.
 * @param r Rectangle to fill.
 * @return False if the image is not available, in which case nothing is drawn.
 */
bool DrawFaceImage(std::string_view id, const Rect &r)
{
#ifdef WITH_PNG
	const FaceImageInfo *info = FindFaceImage(id);
	if (info == nullptr || r.Width() <= 0 || r.Height() <= 0) return false;

	const Blitter *blitter = BlitterFactory::GetCurrentBlitter();
	if (blitter != _face_image_cache_blitter) {
		_face_image_cache.clear();
		_face_image_cache_blitter = blitter;
	}

	auto key = std::make_tuple(info->id, static_cast<uint>(r.Width()), static_cast<uint>(r.Height()));
	auto it = _face_image_cache.find(key);
	if (it == _face_image_cache.end()) {
		it = _face_image_cache.emplace(key, LoadFaceSprite(info->path, r.Width(), r.Height())).first;
	}
	if (it->second == nullptr) return false;

	DrawEncodedSprite(reinterpret_cast<const Sprite *>(it->second.get()), r.left, r.top);
	return true;
#else
	return false;
#endif /* WITH_PNG */
}
