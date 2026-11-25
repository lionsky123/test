# Phoenix Forest 360

Phoenix Forest 360 is a Unity app for comparing 2D source videos with three monoscopic 360° versions of two scenes, **PhoenixForest** and **PigWatermelon**, on Meta Quest 3. The app plays the source clips on a flat 16:9 screen and projects the 360° versions onto a sphere so you can look around by turning your head.

## Video menu

The menu has four buttons per scene. All eight clips loop. The six 360° exports use 3840 × 1920 equirectangular frames at 24 fps.

| Button | Playback | PhoenixForest | PigWatermelon |
| --- | --- | --- | --- |
| **Original (2D)** | Flat screen | [`PhoenixForest.mp4`](Assets/PhoenixForest/PhoenixForest.mp4) | [`PigWatermelon.mp4`](Assets/PhoenixForest/PigWatermelon.mp4) |
| **Imagine360** | 360° | [`PhoenixForest_Imagine360_Test.mp4`](Assets/PhoenixForest/PhoenixForest_Imagine360_Test.mp4) | [`PigWatermelon_Imagine360_Test.mp4`](Assets/PhoenixForest/PigWatermelon_Imagine360_Test.mp4) |
| **Proposed workflow** | 360° | [`PhoenixForest_Proposed_Test.mp4`](Assets/PhoenixForest/PhoenixForest_Proposed_Test.mp4) | [`PigWatermelon_Proposed_Test.mp4`](Assets/PhoenixForest/PigWatermelon_Proposed_Test.mp4) |
| **Argus** | 360° | [`PhoenixForest_Argus_Test.mp4`](Assets/PhoenixForest/PhoenixForest_Argus_Test.mp4) | [`PigWatermelon_Argus_Test.mp4`](Assets/PhoenixForest/PigWatermelon_Argus_Test.mp4) |

## Build and run on Quest 3

1. Install Git LFS and Unity **6000.3.24f1** with **Android Build Support**, **Android SDK & NDK Tools**, and **OpenJDK**.
2. Clone the repository and fetch the videos.

   ```sh
   git lfs install
   git clone https://github.com/lionsky123/test.git
   cd test
   git lfs pull
   ```

3. Open the project in Unity Hub. In **File > Build Profiles**, switch to **Android**. The project is configured for OpenXR with Meta Quest support. The startup scene is [`testScene.unity`](Assets/Scenes/testScene.unity).
4. Connect a Quest 3 with Developer Mode enabled and use **Build and Run**. The **PhoenixForest Proposed workflow** clip starts automatically. Point either Touch controller at a button and squeeze the index trigger when the outline turns green.

## Conversion and benchmark files

- The root-level [`PhoenixForest.mp4`](PhoenixForest.mp4) is the original 960 × 540 perspective clip used by the conversion example below. The corresponding asset in the video menu is a Unity-compatible H.264 4:2:0 playback copy.
- [`video_to_360.py`](video_to_360.py) converts a perspective video into a monoscopic 360° MP4. It detects shots, preserves the recorded background where possible, uses a local FLUX model to extend the background into a panorama, and composites the segmented foreground back onto each frame.
- [`PhoenixForest_Argus_Benchmark/`](PhoenixForest_Argus_Benchmark/) contains the Argus and Proposed workflow panoramas, [per-run results](PhoenixForest_Argus_Benchmark/PhoenixForest_benchmark_results.csv), and [an archive of the benchmark configuration, commands, logs, and GPU traces](PhoenixForest_Argus_Benchmark/PhoenixForest_benchmark_logs.zip). The results report generation time and GPU board memory measured on an NVIDIA GeForce RTX 5080.

### Run the conversion script

The script uses Python 3.12 through `uv`. Set up ComfyUI 0.9.2 with its Python dependencies and the FLUX weights listed in `FluxPanorama.CHECKPOINTS` in the script. If ComfyUI is installed outside the script's default runtime directory, set `VIDEO360_COMFY_ROOT` to the directory containing ComfyUI's `main.py`.

On Windows, set `PYTHONUTF8=1` if Python console output uses a legacy encoding.

```sh
uv run --python 3.12 video_to_360.py PhoenixForest.mp4 --output PhoenixForest_360.mp4
```

The first run can download the BLIP captioning and U²-Net foreground models. Once these models are cached, pass `--offline` to use the cached copies.
