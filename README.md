# Phoenix Forest 360

Phoenix Forest 360 is a Unity project for comparing two monoscopic 360° video outputs on Meta Quest 3. Both panoramas were made from the same four-second perspective clip. The app projects each equirectangular video onto the inside of a sphere so you can look around by turning your head.

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
4. Connect a Quest 3 with Developer Mode enabled and use **Build and Run**. **Xia** starts playing automatically. Point a Touch controller at a button and squeeze the index trigger when the button outline turns green.

| Button | 360° video |
| --- | --- |
| **Argus** | [`PhoenixForest_Argus_Test.mp4`](Assets/PhoenixForest/PhoenixForest_Argus_Test.mp4) |
| **Xia** | [`PhoenixForest_Proposed_Test.mp4`](Assets/PhoenixForest/PhoenixForest_Proposed_Test.mp4) |

Both clips loop and use a 3840 × 1920 equirectangular frame at 24 fps.

## Conversion and benchmark files

- [`PhoenixForest.mp4`](PhoenixForest.mp4) is the original 960 × 540 perspective clip.
- [`video_to_360.py`](video_to_360.py) converts a perspective video into a monoscopic 360° MP4. It detects shots, preserves the recorded background where possible, uses a local FLUX model to extend the background into a panorama, and composites the segmented foreground back onto each frame.
- [`PhoenixForest_Argus_Benchmark/`](PhoenixForest_Argus_Benchmark/) contains both delivered panoramas, [per-run results](PhoenixForest_Argus_Benchmark/PhoenixForest_benchmark_results.csv), and [an archive of the benchmark configuration, commands, logs, and GPU traces](PhoenixForest_Argus_Benchmark/PhoenixForest_benchmark_logs.zip). The results report generation time and GPU board memory measured on an NVIDIA GeForce RTX 5080.

### Run the conversion script

The script uses Python 3.12 through `uv`. Set up ComfyUI 0.9.2 with its Python dependencies and the FLUX weights listed in `FluxPanorama.CHECKPOINTS` in the script. If ComfyUI is installed outside the script's default runtime directory, set `VIDEO360_COMFY_ROOT` to the directory containing ComfyUI's `main.py`.

On Windows, set `PYTHONUTF8=1` if Python console output uses a legacy encoding.

```sh
uv run --python 3.12 video_to_360.py PhoenixForest.mp4 --output PhoenixForest_360.mp4
```

The first run can download the BLIP captioning and U²-Net foreground models. Once these models are cached, pass `--offline` to use the cached copies.
