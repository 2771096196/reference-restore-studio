# H3 便携工作台（Windows x64）

程序包包含独立 Python 3.12、已配好的 ComfyUI 与原图回贴依赖、FFmpeg、中文启动器和3个官方 Real-ESRGAN 轻量超分权重。H3/Krea 等大型模型单独存放，程序更新不需要重新下载模型。

## 使用

推荐在 [GitHub 下载页](https://github.com/2771096196/reference-restore-studio/releases/tag/v0.2.0-portable-preview) 获取 **H3-Portable-Downloader.exe**。运行后选择位置，点击“下载并解压”，下载器会自动获取两个分卷、校验、合并并解压。它只写入所选目录，不修改系统环境；请预留约12 GiB空间用于下载缓存与程序。若手动下载分卷，可放到所选位置下的 `H3-Portable-downloads` 文件夹，下载器会校验并复用。不要单独解压 `.001` 或 `.002`。

1. 完整解压程序包到可写目录，双击 `H3便携启动器.exe`。不要从压缩包内部运行，也不要单独移动 EXE。
2. 已有 `models` 文件夹时，点击“选择已有模型目录”，选择包含 `diffusion_models`、`text_encoders`、`vae` 等子目录的根目录。可以使用别的盘符，默认使用程序旁的 `models`。
3. 下载了散装权重时，点击“导入已下载权重”，或放到程序旁的 `inbox`。完成文件会按大小和 SHA-256 校验后自动复制到正确位置，原文件保留。同名不同版本不会覆盖已有模型。
4. 缺少模型时，按用途筛选，点击“下载并归位”或“下载当前组缺失模型”。自动下载支持断点续传和校验；未核实下载链接的版本会显示来源查找页面，可手动获取后导入。
5. 点击启动对应程序。原图回贴的普通处理不需要 H3 模型；只有 AI 超分需要对应的 Real-ESRGAN 权重。

H3 入门流程在 ComfyUI 的工作流列表中，采用已经在 RTX 3060 12GB / 32GB RAM 上验证的 512×288、56帧、4步配置。所需权重以管理页的“H3 入门工作流”分组为准。

## 环境与限制

- 面向 Windows 10/11 x64。软件不依赖系统 Python、pip、Git 或 CUDA Toolkit；GPU 仍需要兼容 CUDA 13 的 NVIDIA 驱动，缺少 GPU 时原图回贴可选择 CPU。
- 通过“运行环境检查”验证 Python、PyTorch、GPU、FFmpeg。日志在 `logs`。
- 默认端口：ComfyUI 8188，原图回贴8191，管理页8195起。其他实例占用端口时会提示，不会冒充启动成功。
- 修改 `portable-config.json` 的 `ports` 可更换应用端口，如 `{"ports":{"comfyui":18188,"retouch":18191}}`。修改后重新运行启动器。
- 收件箱每5秒检查一次，仅处理稳定且大小与清单一致的完整权重，不处理 `.part`、`.crdownload`。未知版本不会乱放，可在模型清单中查看应使用的文件名与校验值。
- 整套模型很大。复用目录无需额外空间；复制归位需要目标磁盘有足够空间。下载文件由来源站点提供，访问限制或授权请在来源网页处理。
- 所有权重遵循各自模型许可证。仅预置3个官方 Real-ESRGAN 权重并保留许可；不打包用户的大型模型、原图、视频、工程、API密钥或浏览器数据。

## 附带节点与可选扩展

附带 ComfyUI-GGUF、ComfyUI-KJNodes、rgthree-comfy、TE-Speed-MiniMaxH3-OSS、comfyui-krea2edit，保留各自许可证和来源，具体版本见根目录 `BUILD-INFO.json`。

**TE_MAN 不允许未经作者书面授权再分发**，comfyUI-llama-TE 未提供明确许可证，因此本包不附带这两项。原来的复杂整合流依赖这些扩展时，需要按作者渠道自行获取。包内提供的 H3 入门流只依赖 ComfyUI 核心和 ComfyUI-GGUF。

- TE_MAN：https://github.com/tl2012tl/TE_MAN
- comfyUI-llama-TE：https://github.com/tl2012tl/comfyUI-llama-TE

ComfyUI 及节点许可证保留在对应源码目录；Python许可证在 `runtime`，Python依赖许可证在各 `.dist-info` 目录；FFmpeg许可证及源码取得说明在 `licenses`。

## 构建

开发者在装有对应依赖的 Windows 机器上运行：

```powershell
python portable/build_portable.py --source-h3 <已验证安装目录> --output <新的输出目录> --cache <下载缓存目录>
```

公开包仅包含干净的源码、固定版本运行时和可再分发的组件。构建后的包需检查重定位、模型导入、下载恢复、两个服务以及实际模型推理，不能仅凭启动器窗口出现判断成功。
