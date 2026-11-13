# 草稿的外部资源缓存：JSON 不等于成片

剪映草稿的 `draft_content.json` 只保存**引用路径**，不保存字体、特效包、蒙版包、贴纸和素材文件本身。JSON 校验通过，不代表剪映能正确渲染。

最典型的故障：顶部模糊的矩形蒙版包丢失后，草稿里的 `effect_mask` 配置仍然完好，但剪映加载不到蒙版资源，会把「模糊」按**全屏**应用。

## 1. 草稿会引用哪些外部资源

| 资源 | 字段 | 典型位置 |
|---|---|---|
| 视频/音频素材 | `materials.videos[].path`、`audios[].path` | 用户素材目录 |
| 字体 | `texts[].font_path` | 剪映 Cache/effect/.../*.ttf |
| 画面特效 | `video_effects[].path` | `Cache/effect/<effect_id>/<hash>/` |
| 特效蒙版 | `video_effects[].effect_mask[].path` | `Cache/effect/<effect_id>/<md5>/` |
| 贴纸/动画 | `stickers[].path`、`material_animations` | Cache 对应目录 |

路径通常是绝对路径。路径失效时，剪映可能：
- 不显示该特效；
- 显示特效但忽略蒙版（最常见，表现为全屏模糊）；
- 回退到默认字体/默认贴纸。

## 2. 任何批处理前的资源预检

在解密、导出、备份、安装之前，先对草稿跑：

```bash
python scripts/check_resources.py \
  --content "<草稿目录或 draft_content.json>" \
  --jy-draftc "<jy-draftc.exe>" \
  --jy-dir "<JianYing-install-dir>/<version>" \
  --cache-root "C:/Users/<user>/AppData/Local/JianyingPro/User Data/Cache"
```

`jy-draftc` 不在 PATH 时必须传 `--jy-draftc`。

- `missing: 0` 才继续；
- 有缺失时先修复/确认，不要假设“只是预览问题”；
- 对加密草稿，脚本会先解密到临时目录，不改源文件。

## 3. 蒙版包丢失的修复：按 effect_id + md5，不要按 resource_id 猜

`effect_mask` 的 `resource_id` 是逻辑资源号，**不等于缓存目录名，也不保证只有一套包**。同一个 `resource_id` 可能对应多个 `effect_id` / md5：

| 用途 | effect_id | md5 |
|---|---|---|
| 效果蒙版（模糊→矩形用的） | `77064322` | `6e7fa46ea829d76af5c4b45b3af75696` |
| 复杂视频蒙版（另一套） | `82432980` | `1270adfa22a3bb42f179b0a089714d74` |

`Cache/ArtistEffect/<resource_id>/<hash>/` 往往是另一套包（例如复杂视频蒙版）。**按 resource_id 盲复制到 `Cache/effect/...` 会让模糊直接消失或仍然全屏**——这是本次踩过的坑。

正确步骤：

1. 在剪映资源库 DB 里找 `effect_mask` 条目：
   - `Cache/ressdk_db/<account>/rp.db` 的 `http_cache` 表；
   - URL 含 `effect_mask`，响应 JSON 在 `data.effect_item_list[]`；
   - 按 `common_attr.id == resource_id` 且 `extra` 含 `effect_mask` 过滤。
2. 取 `common_attr.effect_id`、`common_attr.md5`、`common_attr.item_urls[0]`。
3. 下载 ZIP，校验 MD5 必须等于 `md5`（通常也是 URL 最后一段）。
4. 解压到 `Cache/effect/<effect_id>/<md5>/`，生成 `<md5>_modity_time.txt`，每行 `<文件绝对路径>, <mtime>`。
5. 草稿 `effect_mask[].path` 指向该目录；完全退出剪映再重开。

`check_resources.py --fix-mask-cache` 已实现 1–4 步：

```bash
python scripts/check_resources.py --content "<草稿>" --fix-mask-cache
```

不要直接删除整个 `Cache` 目录来“重置”。

如果**重新删除再添加同一种蒙版仍然无效，但换成另一种形状就生效**，先确认 DB 里的 `effect_id`/`md5` 与实际缓存目录一致；仍不一致才考虑修客户端或换形状。换形状只是隔离手段，不是根因修复。

## 4. 备份必须包含资源清单

只备份 `draft_content.json` / `draft_meta_info.json` 不够。批量备份至少要有：

- 每条时间线的加密原文 + 解密明文；
- `check_resources.py` 的预检结果（`missing: 0`）；
- 所有被引用资源路径的清单（路径、类型、是否存在、resource_id）；
- 对关键特效/蒙版包，记录 `effect_id`、`md5`、`item_urls`，或直接备份 `Cache/effect/<effect_id>/<md5>/`。

恢复顺序：先恢复草稿 JSON，再恢复/补齐资源缓存，最后开剪映验证。只恢复 JSON 会出现“文件没问题但渲染不对”。

## 5. 不要对正在使用的工程直接跑引擎

`jy-draftc -dc` / `-ex` 会调用剪映引擎。对**正在被剪映打开、正在编辑的工程目录**直接跑，可能触发缓存清理或文件重写，造成：

- 草稿被重新加密（内容可能没变，但哈希变了）；
- 特效/蒙版缓存包被清掉；
- 剪映生成 `.bak`、`template-2.tmp`、`draft.extra` 等临时文件。

批量操作的正确做法：

1. 先退出剪映，或至少不要打开目标工程；
2. 把 `Timelines/<id>` 复制到独立工作目录；
3. 对副本做解密/导出/分析；
4. 需要安装时再写回工程，写完让用户重启剪映；
5. 操作前后各跑一次 `check_resources.py`。

## 6. 验证的边界

- `check_preset.py` 只验证模板参数，不验证资源是否存在；
- headless 导出**不识别顶部蒙版**，导出的 MP4 会整帧模糊，不能用来判断蒙版是否正常；
- 蒙版是否生效，只能在剪映界面里目视确认，或对比草稿的 `effect_mask` + 资源存在性；
- 不要用“导出没黑帧/有声音”代替蒙版验收。

## 7. 本次事故的可验证事实

- 时间线03的解密 JSON 与备份基线 SHA 一致，`effect_mask` 的高度 `0.1459`、中心 Y `0.7025` 仍在；
- 草稿引用的是 `Cache/effect/77064322/6e7fa46ea829d76af5c4b45b3af75696`；
- 该目录缺失后，模糊变成全屏；
- 第一次从 `Cache/ArtistEffect/7356934318301647410/1270adfa...` 复制，**包不对**：那是 `effect_id=82432980`、md5 `1270adfa...` 的复杂视频蒙版；结果模糊直接消失/仍然无蒙版；
- 查询 `ressdk_db/*/rp.db` 的 `http_cache`，`effect_mask` 条目为 `effect_id=77064322`、md5 `6e7fa...`、官方 CDN `item_urls`；
- 下载 ZIP 后 MD5 与 `6e7fa...` 完全一致；解压到 `Cache/effect/77064322/6e7fa...` 并生成清单；
- 重启剪映后矩形蒙版恢复；
- 结论：不是草稿内容被改，也不是“矩形形状本身坏了”，而是缓存放了**另一套同 resource_id、不同 effect_id/md5 的包**。

## 8. 检查清单

- [ ] 操作前退出剪映，或只操作时间线副本
- [ ] `check_resources.py` 输出 `missing: 0`
- [ ] 备份包含资源清单；关键蒙版/特效包记录 effect_id/md5/URL
- [ ] 修复蒙版时按 `effect_id + md5` 匹配，不按 `resource_id` 盲复制
- [ ] 安装后重启剪映，目视确认顶部模糊只盖上部
- [ ] 不把 headless 导出的整帧模糊当成草稿蒙版失效
- [ ] 音效/特效用 `verify_render_ab.py check` 对拍过（窗时间取自草稿段 `target_timerange`，不抄 `manual` 的母带时间）；缺资源包的名字由 `build_draft` 拒绝放置并打 WARN，不会静默变成空特效
- [ ] 修复后仍无效，才考虑修客户端或换形状（换形状是隔离，不是根因修复）
