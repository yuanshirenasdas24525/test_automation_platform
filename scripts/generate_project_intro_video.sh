#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT_DIR/data/generated/project_intro_video"
CLIP_DIR="$OUT_DIR/clips"
TEXT_DIR="$OUT_DIR/text"

mkdir -p "$CLIP_DIR" "$TEXT_DIR"

FONT="/System/Library/Fonts/STHeiti Medium.ttc"
LOGO="$ROOT_DIR/frontend/public/brand-logo.png"
BG="$ROOT_DIR/data/images/im01.jpg"
SCREENSHOT="$ROOT_DIR/data/screenshots/1779328099984_step277_web_goto_failure.png"
OUTPUT="$OUT_DIR/automation_test_platform_intro.mp4"

write_text() {
  local name="$1"
  local text="$2"
  printf "%b" "$text" > "$TEXT_DIR/$name.txt"
}

write_text title_01 "AI 驱动的全栈测试平台"
write_text body_01 "从需求、用例、自动化执行到报告分析\n把测试交付流程放进同一个工作台"

write_text title_02 "统一管理项目交付"
write_text body_02 "项目 / 版本 / 模块 / 需求 / 任务 / Bug\n父子需求、编辑历史、附件\n与负责人清晰追踪"

write_text title_03 "一条自动化执行链路"
write_text body_03 "API / Web / Android / iOS / Mixed\n用例统一编排\nPytest + Celery 执行\nAllure 结果自动同步入库"

write_text title_04 "AI 让测试前移"
write_text body_04 "需求分析生成测试维度\nAI 一键生成测试用例草稿\n审核后批量入库，减少重复设计成本"

write_text title_05 "AI Studio 编码协作"
write_text body_05 "对话式写需求，沉淀结构化草稿\n结合 RAG 检索代码上下文，生成可审核的代码 Diff"

write_text title_06 "Bug 一键修复闭环"
write_text body_06 "AI Agent 自动拉取仓库、定位问题\n提交修复并推送分支\n修复结果回写 Bug，形成研发测试协同闭环"

write_text title_07 "Automation Test Platform"
write_text body_07 "让测试平台不只记录问题\n也能理解需求、生成用例\n执行验证，并推动修复"

NARRATION="$OUT_DIR/narration.txt"
cat > "$NARRATION" <<'TEXT'
这是一个 AI 驱动的全栈自动化测试平台。
它把项目、版本、模块、需求、任务和 Bug 放进同一个工作台，让交付过程更清楚。
在执行层，API、Web、安卓、iOS 和混合用例，走统一的自动化执行链路。
平台通过 Pytest 和 Celery 调度任务，并把 Allure 结果同步到数据库，形成可追踪的执行报告。
在 AI 能力上，它可以分析需求、生成测试维度，也可以一键生成测试用例草稿，审核后批量入库。
AI Studio 支持对话式写需求，并结合 RAG 检索代码上下文，生成可审核的代码 Diff。
当 Bug 出现时，AI Agent 可以自动拉取仓库、定位问题、提交修复并推送分支。
这不只是一个测试管理系统，而是一条从需求理解、用例设计、自动执行到缺陷修复的智能闭环。
TEXT

make_slide() {
  local index="$1"
  local duration="$2"
  local title="$3"
  local body="$4"
  local variant="$5"
  local output="$CLIP_DIR/slide_${index}.mp4"

  if [[ "$variant" == "hero" ]]; then
    ffmpeg -y -hide_banner -loglevel error \
      -loop 1 -t "$duration" -i "$BG" \
      -loop 1 -t "$duration" -i "$LOGO" \
      -filter_complex "\
[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,eq=brightness=-0.18:saturation=0.72,boxblur=3:1[bg];\
color=c=0x07111f@0.58:s=1920x1080:d=${duration}[veil];\
[bg][veil]overlay=format=auto[base];\
[1:v]scale=360:-1[logo];\
[base][logo]overlay=x=120:y=100,\
drawtext=fontfile='${FONT}':textfile='${TEXT_DIR}/${title}.txt':fontcolor=white:fontsize=72:line_spacing=12:x=120:y=390,\
drawtext=fontfile='${FONT}':textfile='${TEXT_DIR}/${body}.txt':fontcolor=0xd9e5ff:fontsize=38:line_spacing=18:x=124:y=510,\
drawbox=x=120:y=700:w=520:h=4:color=0x38bdf8@0.95:t=fill,\
format=yuv420p[v]" \
      -map "[v]" -r 30 -c:v libx264 -pix_fmt yuv420p "$output"
    return
  fi

  if [[ "$variant" == "screenshot" ]]; then
    ffmpeg -y -hide_banner -loglevel error \
      -loop 1 -t "$duration" -i "$SCREENSHOT" \
      -filter_complex "\
color=c=0x0a1020:s=1920x1080:d=${duration}[bg];\
[0:v]scale=980:-1,setsar=1[shot];\
[bg]drawbox=x=0:y=0:w=1920:h=1080:color=0x0a1020@1:t=fill,\
drawbox=x=102:y=94:w=1016:h=632:color=0xffffff@0.10:t=fill,\
drawbox=x=112:y=104:w=996:h=612:color=0xffffff@0.10:t=2[base];\
[base][shot]overlay=x=120:y=120,\
drawtext=fontfile='${FONT}':textfile='${TEXT_DIR}/${title}.txt':fontcolor=white:fontsize=58:line_spacing=10:x=1190:y=160,\
drawtext=fontfile='${FONT}':textfile='${TEXT_DIR}/${body}.txt':fontcolor=0xdbeafe:fontsize=34:line_spacing=16:x=1195:y=280,\
drawbox=x=1195:y=560:w=420:h=4:color=0x22c55e@0.95:t=fill,\
format=yuv420p[v]" \
      -map "[v]" -r 30 -c:v libx264 -pix_fmt yuv420p "$output"
    return
  fi

  ffmpeg -y -hide_banner -loglevel error \
    -f lavfi -i "color=c=0x08111f:s=1920x1080:d=${duration}" \
    -loop 1 -t "$duration" -i "$LOGO" \
    -filter_complex "\
[1:v]scale=250:-1,format=rgba,colorchannelmixer=aa=0.26[mark];\
[0:v]drawbox=x=0:y=0:w=1920:h=1080:color=0x08111f@1:t=fill,\
drawbox=x=0:y=0:w=1920:h=1080:color=0x0f766e@0.12:t=fill,\
drawbox=x=118:y=150:w=12:h=690:color=0x38bdf8@0.95:t=fill,\
drawbox=x=154:y=145:w=1050:h=700:color=0xffffff@0.045:t=fill[base];\
[base][mark]overlay=x=1510:y=760,\
drawtext=fontfile='${FONT}':textfile='${TEXT_DIR}/${title}.txt':fontcolor=white:fontsize=62:line_spacing=10:x=190:y=230,\
drawtext=fontfile='${FONT}':textfile='${TEXT_DIR}/${body}.txt':fontcolor=0xdbeafe:fontsize=38:line_spacing=20:x=195:y=370,\
drawbox=x=195:y=665:w=560:h=4:color=0x22c55e@0.95:t=fill,\
format=yuv420p[v]" \
    -map "[v]" -r 30 -c:v libx264 -pix_fmt yuv420p "$output"
}

make_slide "01" 6 "title_01" "body_01" "hero"
make_slide "02" 6 "title_02" "body_02" "normal"
make_slide "03" 7 "title_03" "body_03" "normal"
make_slide "04" 6 "title_04" "body_04" "normal"
make_slide "05" 6 "title_05" "body_05" "normal"
make_slide "06" 7 "title_06" "body_06" "normal"
make_slide "07" 6 "title_07" "body_07" "hero"

CONCAT="$OUT_DIR/concat.txt"
: > "$CONCAT"
for clip in "$CLIP_DIR"/slide_*.mp4; do
  printf "file '%s'\n" "$clip" >> "$CONCAT"
done

say -v Tingting -r 175 -f "$NARRATION" -o "$OUT_DIR/narration.aiff"

ffmpeg -y -hide_banner -loglevel error \
  -f concat -safe 0 -i "$CONCAT" \
  -i "$OUT_DIR/narration.aiff" \
  -filter_complex "[0:v]fade=t=in:st=0:d=0.35,fade=t=out:st=43.4:d=0.6[v];[1:a]apad,atrim=0:44[a]" \
  -map "[v]" -map "[a]" \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -b:a 160k -shortest "$OUTPUT"

echo "$OUTPUT"
