# 封面资源包读取加速计划

## Summary

计划文件地址：`D:\Codex\2026-07-11\to\work\superfw-cn\docs\coverart-pack-acceleration-plan.md`

将封面系统改造成一个深模块：菜单继续使用现有 `coverart_*` 接口，资源包索引、批量读取、校验和状态管理全部封装在模块内部。

- 固件只读取 `/.superfw/covers.pak`，不再读取 BMP 或 CVR1 缓存。
- 保持当前 216 色 8bpp 画质。
- 每帧最多连续读取 8 个扇区，即 4096 字节。
- 资源包缺失、损坏或没有对应游戏码时静默不显示封面。
- 普通 SD 浏览仍读取一次 ROM 头获取游戏码；Flash 列表继续直接使用已有游戏码。

## Pack Format

资源包由新版 `tools/coverart-pack.py <card-root>` 从 `/IMGS/{c0}/{c1}/{CODE}.bmp` 生成，并通过临时文件加原子替换写入 `/.superfw/covers.pak`。

所有整数均为小端；游戏码转换为可数值排序的 `c0<<24 | c1<<16 | c2<<8 | c3`。

```text
Sector 0: CVPK global header, exactly 512 bytes
Page directory: first game-code key of each index page, padded to 512 bytes
Index pages: 512 bytes each, 64 entries per page
Cover blocks: 512-byte CVR2 header + packed pixels + sector padding
```

全局头字段：

```c
magic="CVPK", version=1, header_size=512
entry_count, entries_per_page=64, page_count
page_directory_offset=512
index_offset, data_offset, total_file_size
```

索引条目固定为 8 字节：

```c
struct CoverIndexEntry {
    uint32_t gamecode_key;
    uint32_t cover_offset; // absolute, 512-byte aligned
};
```

每个封面块的首扇区：

```c
magic="CVR2", gamecode_key
uint16_t width, height
uint32_t pixel_bytes       // must equal width * height
uint16_t palette_count     // exactly 216
uint16_t pixel_format      // 1 = biased 8bpp
uint16_t palette[216]      // GBA BGR555
reserved zeroes to byte 511
```

像素从下一扇区开始，按屏幕显示顺序紧密存放 `width*height` 字节，值域为 `20..235`，末尾补零到扇区边界。限制为 512 个索引页、32768 张封面；重复游戏码、非法尺寸或不支持的 BMP 使生成器失败，不产生半成品资源包。

## Firmware Changes

- 保持 `coverart_update`、`coverart_update_gcode`、`coverart_pump`、查询和绘制接口不变；删除运行时中值切分、BMP 双遍扫描、CVR1 校验和缓存写入实现。
- 首次请求时懒加载资源包：打开文件、校验全局头、读取最多 2KB 的页目录。资源包 `FIL` 在菜单运行期间保持打开。
- 用页目录二分查找目标索引页；读取一个512字节索引页并在内存中二分查找。缓存最近使用的索引页。
- 读取并校验 CVR2 首扇区，包括魔数、游戏码、尺寸、像素长度、偏移对齐和文件范围；无效条目只终止当前请求。
- 从对齐的像素起点直接向 `cover_pix` 紧密读取：每次 `f_read` 最多4096字节，使 FatFs 进入多扇区直读路径。最后一批允许不足4096字节。
- 全部读取成功后，从最后一行向第一行执行重叠安全的行展开，将紧密布局转换为 `COVER_MAX_W` 步长，再提交调色板和 `cover_have=true`。
- 新选择到达时立即取消旧请求并跳转到新索引查找；资源包句柄和已加载页目录继续复用。
- `FR_NO_FILE`、格式错误和缺失游戏码不重试。磁盘错误关闭并重开资源包，间隔20帧、最多重试3次；失败后当前项目无封面，新选择可以重新尝试。
- `coverart_invalidate` 清除显示和当前请求，但保留已经验证的全局头、页目录及资源包句柄。

删除旧实现后释放的量化直方图、颜色累计数组和缓存写入状态可覆盖新增的2KB页目录、512字节索引页缓存和512字节封面头，不增加总体内存压力。

## Tooling And Migration

- 将现有生成器改造并更名为 `coverart-pack.py`，保留相同的 BMP 量化结果，不再生成 `.superfw/imgcache/*.img`。
- 生成器按游戏码排序、构建分页索引、对齐每个封面块，并在替换目标文件前重新解析输出，验证所有偏移、尺寸和条目数量。
- 更新使用文档：升级固件前必须运行一次生成器；旧 `/IMGS` 仍作为生成输入，旧 `imgcache` 可由用户自行删除，但固件忽略它。
- 不自动迁移或运行时重建资源包，避免 GBA 上进行大文件重写、索引更新和掉电一致性处理。

## Test Plan

- Python 格式测试：空包、单条目、65条目跨页、首/中/末条目、缺失游戏码、重复游戏码、非法 BMP、确定性输出和原子替换。
- 固件解析测试：损坏魔数、错误版本、未对齐偏移、越界块、尺寸超限、截断像素和无效调色板数量均应静默失败且不越界。
- mGBA 端到端测试：生成含已知红色/蓝色封面的资源包，在 SD 浏览、最近游戏和 Flash 列表验证截图像素、尺寸和右对齐位置。
- 切换测试：连续快速移动选择，确认只有最后请求能够提交画面，无失效 `FIL`、错封面或残留调色板。
- I/O 验证：跟踪典型 `120x75` 封面，确认像素阶段为 `4096 + 4096 + 808` 字节三批读取，而非逐行读取。
- 构建并运行现有主机测试；完成 SuperChis 全量固件构建和最终 mGBA 烟测。
- 真机验收：资源包预热后，直接游戏码请求不超过7个渲染帧，普通 SD ROM 不超过9帧；快速浏览保持菜单动画连续，三个列表均能稳定显示完整封面。

## Assumptions

- 首版不加入4bpp、LZ4、邻项预取、ROM路径到游戏码索引或底层CMD17优化。
- 资源包在GBA运行期间不会被外部修改，因此无需热重载。
- 缺少资源包时不显示错误提示，也不回退旧格式。
- 完成实现与验证后，仅提交相关源码、工具、测试和计划文档，不提交测试SD镜像或生成缓存。
