/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file face_images.h Company manager face images loaded from the faces/ folder. */

#ifndef FACE_IMAGES_H
#define FACE_IMAGES_H

#include "core/geometry_type.hpp"

#include <vector>

/**
 * A face image found in the faces/ folder.
 * The file name (without extension) is `<type>_<category>_<name>`, e.g. `real_president_donald_trump`.
 */
struct FaceImageInfo {
	std::string id; ///< File name without extension; the identity stored in the company face.
	std::string path; ///< Full path of the file.
	std::string type; ///< Type, e.g. "real", "anime", "cartoon".
	std::string category; ///< Category, e.g. "president".
	std::string name; ///< Display name, e.g. "Donald Trump".
};

struct CompanyManagerFace;

static const uint MAX_FACE_IMAGE_ID_LENGTH = 255; ///< Maximum length of a face image id in bytes.

void ScanFaceImages();
const std::vector<FaceImageInfo> &GetFaceImages();
const FaceImageInfo *FindFaceImage(std::string_view id);
const FaceImageInfo *GetAutoFaceImage(uint32_t seed);
const FaceImageInfo *GetCompanyFaceImage(const CompanyManagerFace &cmf);
bool IsValidFaceImageSetting(std::string_view setting);
std::string FormatFaceImageWord(std::string_view word);

bool DrawFaceImage(std::string_view id, const Rect &r);
void ClearFaceImageCache();

#endif /* FACE_IMAGES_H */
