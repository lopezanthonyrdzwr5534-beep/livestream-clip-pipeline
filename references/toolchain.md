# 工具链与安装

## 1. 工程结构（11.x）

```
Project/
├── draft_content.json
├── draft_meta_info.json
└── Timelines/
    ├── project.json
    ├── <timeline-01>/draft_content.json   # 源素材
    └── <timeline-02>/draft_content.json   # 成片
```

- 时间线01 = 整场原素材；成片追加 02+。
- 新时间线目录：`draft_content.json` + 封面即可先验证；必要时补 `draft.extra`/附件。
- `draft_content.json` 的 `id` 必须等于新时间线 id。

## 2. jy-draftc

- `-dc <dir>`：解密内容文件到 `<dir>-plain`
- `-ec <plain-dir>`：回加密
- `-ug/-dg -t <version>`：结构升降级
- `-ex <dir> -o out.mp4`：无头导出
- `--jy-dir` 指向**含 `videoeditor.dll` 的目录**。当前安装版本要用
  `find <JianYing-install-root> -maxdepth 2 -iname videoeditor.dll` 现查，别信文档里写死的版本号。
- **剪映自升级会废掉上一个版本目录**（2026-11-02 实测：11.6→11.7 之后，
  `11.6.0.14552/` 里的 dll 全被改名成 `videoeditor.dll_d_a12106da`）。
  症状分两种：`--jy-dir` 指旧目录 → `videoeditor.dll not found`；
  只把那个改名文件复制成 `videoeditor.dll` → `LoadLibraryExW failed, gle=126`（缺依赖）。
  修法：`scripts/restore_dlls.py --src <旧版本目录> --dst <私有镜像>` 把
  `*_d_<hash>` 按原名整批复制（只读源目录，约 440MB），然后 `--jy-dir <镜像>`。
  密文方案跨版本没变（新旧草稿 `new_version` 都是 189.0.0），所以镜像里旧 dll
  既解得开 11.6 写的草稿，也解得开 11.7 写的，装进去的密文 11.7 照样能读。
- 只处理内容文件；`draft_meta_info.json` 可能被剪映重新加密
- exe 旁边的 `.env` 里 `JY_INSTALL_DIR` 一旦含制表符就报 `videoeditor.dll not found`。
  **别用 `printf`/heredoc 写这个文件**——`\1` 会被 printf 当八进制转义吃掉变成 `\t`，
  `D:\JianyingPro\11.6.0.14552` 就变成 `D:\JianyingPro<TAB>.6.0.14552`。
  用 Write 工具或正斜杠路径写；不放心就每条命令显式带 `--jy-dir`。
- `-ex` 卡住时先看 `Get-Process jy-draftc` 的 CPU：几十分钟只烧几秒 = 阻塞不是慢，
  按 SKILL.md Stage 5 的三步归因（探针草稿 → 复现当天成功过的草稿 → `jy-draftc -c` 自检）。

## 3. 顶部模糊

- `materials.video_effects` 里的 `模糊` 是 video_effect + `effect_mask` 矩形。
- 只盖上部；复制时保留 mask 的 width/height/centerY。
- headless 导出可能不渲染 `effect_mask`，预览看起来像全屏糊；最终以剪映里打开为准。

## 4. 字幕轨

- 全片模糊轨在字幕下面会把字幕盖掉。
- 把字幕轨放数组末尾，`render_index=15000`、`track_render_index=8`。
- 标题轨在模糊之上，但不要压字幕。
- 句间 0 缝（覆盖率优先），防 30fps 同帧叠字靠时间点取整而不是留缝。

## 5. 安装时间线 02+

`install_timeline.py` 已经把下面 1–5 全包了（含加密与回读校验），列在这里是为了看懂它在干什么、以及脚本不可用时手工补装。

1. 备份 `Timelines/project.json`——**文件名必须带微秒或随机后缀**。按秒命名时连装 9 条只会剩 3 个备份，且最早那份（唯一记录"原始单时间线"状态的）会被后面的覆盖掉，回滚就没有干净基线了。
2. 新建 `Timelines/<new-id>/`，写入 `draft_content.json`（`id` 改成 new-id、`name` 改成时间线名）和封面。
3. **剪映 11.x 落盘是密文**：明文 `draft_content.json` 装进去，剪映要么不认这条时间线、要么得重存一遍。所以写完明文必须 `jy-draftc -ec` 再把密文回填。对照检查：目录里 `draft_content.json` 首字节不该是 `{`。
4. **加密必须在改 id 之后**。拿 `work/draft/` 里的原始草稿直接加密，会把 builder 自己的 id 写进去，注册表和草稿内容对不上，剪映按注册表找目录就找不到内容。
5. 在 `project.json.timelines` 末尾追加 `{create_time,id,name,update_time,is_marked_delete:false}`；`main_timeline_id` 不变；同时写 `project.json.bak`。要按"序号=推荐名次"显示时，追加后把非主时间线按 name 排序（`--sort`）。
6. **回读校验**：`jy-draftc -dc` 解密装好的那条，核对 `id` == 注册表 id、`duration` 与草稿一致，再谈成功。
7. `jy-draftc -ex Timelines/<new-id>` 验证（导出本身可能因显卡/GL 状态坏掉而跑不动，见 §6 末）。
8. 让用户重启剪映；不要声称已显示除非确认。

## 6. 验证

- `ffprobe` 查时长/分辨率/音频。
- `ffmpeg -vf blackdetect=d=0.02` 全程扫黑帧。
- 抽帧：开头/每个切点/音效点/结尾/长字幕。
- 音效/特效是否生效：`verify_render_ab.py`（窗时间取自草稿段 `target_timerange`，不要抄 `manual` 的母带时间）比音频 RMS 与画面像素差；face_effect 报「无变化」时必须剪映目视。
- 字幕 0 重叠、单行、拼回原句一致。
- 导出成功不等于剪映能打开；最终以剪映里播放为准。
