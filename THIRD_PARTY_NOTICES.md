# Third-party components

The MIT license covers this project's own code. It does not relicense dependencies, model checkpoints, FFmpeg binaries or user media. No third-party model weights or binaries are included in this repository.

- [OpenCV](https://github.com/opencv/opencv): feature matching, optical flow, warping and image operations.
- [NumPy](https://github.com/numpy/numpy), [Pillow](https://github.com/python-pillow/Pillow): image and numerical processing.
- [PyAV](https://github.com/PyAV-Org/PyAV), [FFmpeg](https://ffmpeg.org/legal.html): decoding, encoding and audio remuxing. FFmpeg licensing depends on the selected build and enabled libraries.
- [aiohttp](https://github.com/aio-libs/aiohttp): local HTTP service.
- [PyTorch](https://github.com/pytorch/pytorch), [Spandrel](https://github.com/chaiNNer-org/spandrel): optional model inference.
- [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN): optional official general-x4v3, general-wdn-x4v3 and animevideov3 checkpoints from the [v0.2.5.0 release](https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.2.5.0). See upstream license and [anime-video model documentation](https://github.com/xinntao/Real-ESRGAN/blob/master/docs/anime_video_model.md). Downloading is an explicit user action and files are checksum-verified.

Consult upstream licenses for the exact versions you install. This repository does not vendor Real-ESRGAN or BasicSR source. Photoshop-style terminology describes familiar interactions; the project is not affiliated with Adobe.
