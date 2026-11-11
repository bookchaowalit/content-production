# Bundled scene3d assets

| File | Used by | Source | Author | License | sha256 |
|---|---|---|---|---|---|
| `studio_small_09_1k.exr` | lighting `studio-hdri.v1` | <https://polyhaven.com/a/studio_small_09> (1k EXR) | Sergej Majboroda | CC0-1.0 (public domain) | `44dcbed056512ecd8afd2a3d7ba73d2dd4c24694d0c72005fafcb2469bbc8fd4` |

The sha256 is pinned in `scene_builder.HDRI_PRESETS`; the Blender worker
refuses a file that does not match (`hdri_asset_mismatch`). Replacing the HDRI
means a new preset id, not a new file under the old one.
