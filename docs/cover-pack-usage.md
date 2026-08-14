# GBA 封面包用法 / GBA Cover Pack Usage

## 中文

本 Release 的 `superfw-covers.pak` 是 SuperFW 封面浏览器使用的预构建 GBA 包装盒
封面资源包。配套固件仅适用于 SuperCard Chis；WenQuanYi 扩展字库无法装入 SD/Lite 的
ROM 容量。

1. 安装本 Release 中对应的 SuperFW 固件。
2. 若 SD 卡根目录没有 `.superfw` 文件夹，请先创建它。
3. 将 `superfw-covers.pak` 复制为 `/.superfw/covers.pak`。
4. 重启掌机。ROM 的四字符 GBA 游戏码存在于资源包中时，菜单会显示封面。

封面包是可选项。资源包缺失或损坏只会关闭封面显示，不会影响游戏启动。本包包含 2,915
个游戏码条目，包括已核验的兼容中文 ROM 地区封面别名。游戏头部代码无效的 ROM 无法由
固件匹配，因而未包含在别名中。

## English

`superfw-covers.pak` in this Release is a prebuilt GBA box-art resource for
SuperFW's cover browser. The accompanying firmware is for SuperCard Chis
hardware; the WenQuanYi extended font pack does not fit the SD or Lite ROM
budgets.

1. Install the matching SuperFW firmware from this Release.
2. Create a `.superfw` directory at the root of the SD card if it does not
   already exist.
3. Copy `superfw-covers.pak` to `/.superfw/covers.pak` on the SD card.
4. Restart the console. Covers are shown when a ROM's four-character GBA game
   code is present in the package.

The pack is optional. A missing or invalid package only disables cover display;
it does not prevent games from launching. This pack contains 2,915 game-code
entries, including verified regional-cover aliases for compatible Chinese ROMs.
ROMs with invalid game-code headers cannot be matched by the firmware and are
intentionally excluded from the aliases.
