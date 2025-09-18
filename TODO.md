# PhotoVerify TODO

## Video File Support (MP4/MOV/AVI)

### Current Architecture Analysis

PhotoVerify is currently designed around **image files** with these key characteristics:
- Uses `PhotoMetadata` model with image-specific fields (`width`, `height`, `ImageWidth`, `ImageHeight`)
- Extracts `DateTimeOriginal`, `ImageWidth`, `ImageHeight` via exiftool
- Database schema and verification logic assume image files
- Project name and documentation focus on "photos"

### Video Support Feasibility

**✅ EASY aspects:**
- **exiftool already supports video files** - MP4, MOV, AVI are well-supported formats
- **File detection** - Just need to add extensions to `SUPPORTED_EXTENSIONS`
- **Basic metadata** - Can extract creation date, camera make/model, file size, duration
- **Hashing and verification** - Current hash-based verification would work unchanged

**⚠️ MODERATE complexity:**
- **Metadata differences** - Videos have `duration` instead of `width`/`height`, may need `VideoWidth`/`VideoHeight`
- **Database schema** - May want video-specific fields (duration, codec, fps, etc.)
- **Terminology** - "photos" vs "media files" throughout codebase and docs

**🔴 CHALLENGING aspects:**
- **Performance** - Video files are much larger, hash calculation could be very slow
- **Verification logic** - Current fuzzy matching assumes camera naming patterns for photos
- **User expectations** - Video backup workflows may differ significantly from photo workflows

### Implementation Plan

#### Phase 1: Basic Video Support (Minimal Changes)
1. **Add video extensions** to `SUPPORTED_EXTENSIONS` in PhotoScanner
2. **Update exiftool extraction** to handle video metadata fields:
   - Change from `ImageWidth/ImageHeight` to `VideoWidth/VideoHeight` for videos
   - Extract video duration if available
   - Keep existing `DateTimeOriginal`, `Make`, `Model` extraction
3. **Update documentation** to reflect video support in CLAUDE.md

#### Phase 2: Enhanced Video Support (Optional)
4. **Add video-specific database fields**:
   - `duration` field for video length
   - `video_codec` field
   - `frame_rate` field
5. **Update PhotoMetadata model** to handle video-specific fields
6. **Performance optimization** for large video files:
   - Consider sampling-based hashing for very large videos
   - Add progress indicators for slow hash operations

#### Phase 3: Terminology Updates (Optional)
7. **Rename project** from "PhotoVerify" → "MediaVerify"
8. **Update CLI help text** and documentation
9. **Rename `PhotoMetadata` → `MediaMetadata`**

### Recommendation:
Start with **Phase 1 only** for minimal disruption. This gives you video file support with ~10 lines of code changes while maintaining backward compatibility. Phases 2-3 can be added later if needed.

**Estimated effort:** Phase 1 = 30 minutes, Full implementation = 4-6 hours