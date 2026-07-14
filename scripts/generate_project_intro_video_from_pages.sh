#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT_DIR/data/generated/project_intro_video"
SHOT_DIR="$OUT_DIR/page_shots"
CLIP_DIR="$OUT_DIR/page_clips"
TEXT_DIR="$OUT_DIR/page_text"

mkdir -p "$CLIP_DIR" "$TEXT_DIR"

FONT="/System/Library/Fonts/STHeiti Medium.ttc"
LOGO="$ROOT_DIR/frontend/public/brand-logo.png"
OUTPUT="$OUT_DIR/automation_test_platform_intro_real_pages.mp4"

write_text() {
  local name="$1"
  local text="$2"
  printf "%b" "$text" > "$TEXT_DIR/$name.txt"
}

write_text title_01 "自动化测试平台"
write_text body_01 "AI 驱动的 SDLC 测试工作台\n真实页面演示版"

write_text title_02 "项目统一管理"
write_text body_02 "项目、技术栈、用例规模、执行状态\n在一个入口里集中查看"

write_text title_03 "执行记录可追踪"
write_text body_03 "每次运行自动沉淀报告\n支持按分类、状态、项目筛选"

write_text title_04 "设备与安装包"
write_text body_04 "App 自动化需要的设备池与安装包\n统一注册、上传和调度"

write_text title_05 "团队工作台"
write_text body_05 "成员、角色、任务和全局看板\n为研发、测试、产品协作提供入口"

write_text title_06 "AI 能力闭环"
write_text body_06 "需求分析、用例生成、AI Studio 编码协作\n再到 Bug 一键修复"

write_text title_07 "从需求到修复"
write_text body_07 "让测试平台不只记录问题\n也能推动验证和交付闭环"

NARRATION="$OUT_DIR/narration_real_pages.txt"
cat > "$NARRATION" <<'TEXT'
这是自动化测试平台的真实页面演示版。
在项目管理页，可以集中查看项目、技术栈、用例数量、通过率和最近执行状态。
进入执行记录后，每一次运行都会沉淀为可追踪报告，支持按分类、状态和项目筛选。
对于 App 自动化，平台提供设备池和安装包管理，让真机、模拟器和 APK、IPA 都能统一调度。
团队工作台则承载成员、角色、任务和全局看板，为研发、测试、产品协作提供入口。
在 AI 能力上，平台覆盖需求分析、用例生成、AI Studio 编码协作，以及 Bug 一键修复。
最终，它把需求理解、用例设计、自动执行、报告分析和缺陷修复串成一条智能闭环。
TEXT

make_page_slide() {
  local index="$1"
  local duration="$2"
  local image="$3"
  local title="$4"
  local body="$5"
  local output="$CLIP_DIR/slide_${index}.mp4"

  ffmpeg -y -hide_banner -loglevel error \
    -loop 1 -t "$duration" -i "$image" \
    -loop 1 -t "$duration" -i "$LOGO" \
    -filter_complex "\
[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,eq=brightness=-0.08:saturation=0.88[shot];\
color=c=0x07111f@0.68:s=1920x1080:d=${duration}[veil];\
[shot][veil]overlay=format=auto[base];\
[0:v]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0xf8fafc,setsar=1[panel];\
[base]drawbox=x=86:y=98:w=1348:h=788:color=0xffffff@0.14:t=fill,\
drawbox=x=100:y=112:w=1320:h=760:color=0xffffff@0.20:t=2[frame];\
[frame][panel]overlay=x=120:y=132[withpanel];\
[1:v]scale=210:-1,format=rgba,colorchannelmixer=aa=0.35[mark];\
[withpanel][mark]overlay=x=1625:y=840,\
drawbox=x=1488:y=0:w=432:h=1080:color=0x08111f@0.82:t=fill,\
drawtext=fontfile='${FONT}':textfile='${TEXT_DIR}/${title}.txt':fontcolor=white:fontsize=46:line_spacing=10:x=1528:y=170,\
drawtext=fontfile='${FONT}':textfile='${TEXT_DIR}/${body}.txt':fontcolor=0xdbeafe:fontsize=30:line_spacing=16:x=1532:y=285,\
drawbox=x=1532:y=560:w=270:h=4:color=0x22c55e@0.95:t=fill,\
format=yuv420p[v]" \
    -map "[v]" -r 30 -c:v libx264 -pix_fmt yuv420p "$output"
}

make_cover_slide() {
  local output="$CLIP_DIR/slide_01.mp4"
  ffmpeg -y -hide_banner -loglevel error \
    -loop 1 -t 5 -i "$SHOT_DIR/projects.png" \
    -loop 1 -t 5 -i "$LOGO" \
    -filter_complex "\
[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,boxblur=4:1,eq=brightness=-0.2:saturation=0.7[bg];\
color=c=0x07111f@0.62:s=1920x1080:d=5[veil];\
[bg][veil]overlay=format=auto[base];\
[1:v]scale=360:-1[logo];\
[base][logo]overlay=x=120:y=105,\
drawtext=fontfile='${FONT}':textfile='${TEXT_DIR}/title_01.txt':fontcolor=white:fontsize=72:line_spacing=12:x=120:y=410,\
drawtext=fontfile='${FONT}':textfile='${TEXT_DIR}/body_01.txt':fontcolor=0xd9e5ff:fontsize=38:line_spacing=18:x=124:y=535,\
drawbox=x=120:y=735:w=520:h=4:color=0x38bdf8@0.95:t=fill,\
format=yuv420p[v]" \
    -map "[v]" -r 30 -c:v libx264 -pix_fmt yuv420p "$output"
}

make_cover_slide
make_page_slide "02" 6 "$SHOT_DIR/projects.png" "title_02" "body_02"
make_page_slide "03" 6 "$SHOT_DIR/runs.png" "title_03" "body_03"
make_page_slide "04" 6 "$SHOT_DIR/devices.png" "title_04" "body_04"
make_page_slide "05" 6 "$SHOT_DIR/workspace_admin.png" "title_05" "body_05"
make_page_slide "06" 6 "$SHOT_DIR/dashboard.png" "title_06" "body_06"
make_page_slide "07" 5 "$SHOT_DIR/projects.png" "title_07" "body_07"

CONCAT="$OUT_DIR/concat_real_pages.txt"
: > "$CONCAT"
for clip in "$CLIP_DIR"/slide_*.mp4; do
  printf "file '%s'\n" "$clip" >> "$CONCAT"
done

say -v Tingting -r 175 -f "$NARRATION" -o "$OUT_DIR/narration_real_pages.aiff"

ffmpeg -y -hide_banner -loglevel error \
  -f concat -safe 0 -i "$CONCAT" \
  -i "$OUT_DIR/narration_real_pages.aiff" \
  -filter_complex "[0:v]fade=t=in:st=0:d=0.35,fade=t=out:st=39.4:d=0.6[v];[1:a]apad,atrim=0:40[a]" \
  -map "[v]" -map "[a]" \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -b:a 160k -shortest "$OUTPUT"

echo "$OUTPUT"
