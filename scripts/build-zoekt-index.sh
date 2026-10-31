#!/usr/bin/env bash
# 开发期调用原生工具构建；不是 Odoo 解析器，失败不会替换已发布索引。
set -euo pipefail
dy_repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
dy_source="$dy_repo_root/../data"
dy_output="$dy_repo_root/plugins/dy_superpowers/mcp-server/src/dy_superpowers_mcp/data/zoekt"
dy_audit="$dy_repo_root/docs/research-sources/zoekt/build-$(date -u +%Y%m%dT%H%M%SZ)"
while (($#)); do
    case "$1" in
        --source) dy_source="$2"; shift 2 ;;
        --output) dy_output="$2"; shift 2 ;;
        --audit) dy_audit="$2"; shift 2 ;;
        *) printf '未知参数：%s\n' "$1" >&2; exit 2 ;;
    esac
done
for dy_tool in rsync jq iconv python3 sha256sum; do command -v "$dy_tool" >/dev/null; done
: "${ZOEKT_INDEX_BIN:?请指定开发期 zoekt-index 路径}"
: "${ZOEKT_SERVER_BIN:?请指定 zoekt-webserver 路径}"
: "${GITLEAKS_BIN:?请指定开发期 gitleaks 路径}"
for dy_tool in "$ZOEKT_INDEX_BIN" "$ZOEKT_SERVER_BIN" "$GITLEAKS_BIN"; do test -x "$dy_tool"; done
dy_source="$(realpath -e -- "$dy_source")"
dy_output="$(realpath -m -- "$dy_output")"
dy_audit="$(realpath -m -- "$dy_audit")"
case "$dy_audit/" in "$dy_repo_root/docs/research-sources/"*) ;; *) printf '审计必须位于 docs/research-sources/\n' >&2; exit 2 ;; esac
case "$dy_output/" in "$dy_source/"*) printf '索引不得写入输入源码目录\n' >&2; exit 2 ;; esac
test ! -L "$dy_output"
mkdir -p -- "$dy_audit"
dy_stage="$(mktemp -d /tmp/dy-zoekt-build-XXXXXXXX)"
# 保留失败临时副本供诊断；不自动删除任何输入或旧索引。
mkdir -- "$dy_stage/hrp" "$dy_stage/index"
printf '%s\n' "$dy_stage" > "$dy_audit/build-directory.txt"
rsync -r --prune-empty-dirs --filter="merge $dy_repo_root/scripts/zoekt-source.filter" \
    "$dy_source/" "$dy_stage/hrp/" > "$dy_audit/copy.log"
find "$dy_source" -type f -printf '%P\0' | sort -z | jq -Rs 'split("\u0000")[:-1]' > "$dy_audit/input-files.json"
find "$dy_stage/hrp" -type f -printf '%P\0' | sort -z | jq -Rs 'split("\u0000")[:-1]' > "$dy_audit/included-files.json"
find "$dy_source" -type l -printf '%P\0' | sort -z | jq -Rs 'split("\u0000")[:-1]' > "$dy_audit/symlinks.json"
jq -n --slurpfile all "$dy_audit/input-files.json" --slurpfile included "$dy_audit/included-files.json" \
    '{included:($included[0]|length), excluded:($all[0]-$included[0]|map({path:.,reason:"打包文件策略排除；具体规则见构建所用 filter"}))}' \
    > "$dy_audit/coverage.json"
if [[ "$(jq length "$dy_audit/included-files.json")" == 0 ]]; then printf '没有可收录源码\n' >&2; exit 1; fi
printf '' > "$dy_audit/encoding-conversions.jsonl"
while IFS= read -r -d '' dy_file; do
    if ! iconv -f UTF-8 -t UTF-8 "$dy_file" -o /dev/null 2>/dev/null; then
        dy_encoding="$(LC_ALL=C sed -n '1s/.*encoding=["\x27]\([^"\x27]*\)["\x27].*/\1/p' "$dy_file")"
        if [[ "$dy_file" != *.xml || "$dy_encoding" != GB2312 ]]; then
            printf '源码编码未知，停止构建：%s\n' "${dy_file#"$dy_stage/hrp/"}" >&2; exit 1
        fi
        iconv -f GB2312 -t UTF-8 "$dy_file" -o "$dy_stage/converted"
        mv -- "$dy_stage/converted" "$dy_file"
        jq -nc --arg path "${dy_file#"$dy_stage/hrp/"}" '{path:$path,original_encoding:"GB2312",storage_encoding:"UTF-8"}' \
            >> "$dy_audit/encoding-conversions.jsonl"
    fi
done < <(find "$dy_stage/hrp" -type f -print0)
dy_scan_status=0
"$GITLEAKS_BIN" dir "$dy_stage/hrp" --config "$dy_repo_root/scripts/zoekt-gitleaks.toml" \
    --gitleaks-ignore-path /dev/null --ignore-gitleaks-allow --redact=100 --no-banner --no-color \
    --log-level error --report-format json --report-path "$dy_stage/scan-redacted.json" || dy_scan_status=$?
if [[ ! -f "$dy_stage/scan-redacted.json" ]]; then printf '凭据扫描失败，未产生可校验报告\n' >&2; exit 1; fi
jq --argjson exit_code "$dy_scan_status" \
    '{status:(if $exit_code==0 then "passed" else "blocked" end),exit_code:$exit_code,findings:map({rule:.RuleID,file:.File,line:.StartLine,description:.Description})}' \
    "$dy_stage/scan-redacted.json" > "$dy_audit/scan.json"
if ((dy_scan_status)); then printf '凭据扫描未通过，未构建或替换索引。脱敏报告：%s/scan.json\n' "$dy_audit" >&2; exit 1; fi
jq -n '{Name:"hrp"}' > "$dy_stage/hrp.meta.json"
GOMAXPROCS=2 "$ZOEKT_INDEX_BIN" -disable_ctags -parallelism 1 -large_file '**' \
    -index "$dy_stage/index" -meta "$dy_stage/hrp.meta.json" "$dy_stage/hrp" \
    > "$dy_audit/index.log" 2>&1
python3 "$dy_repo_root/scripts/verify-zoekt-index.py" --snapshot "$dy_stage/hrp" \
    --index "$dy_stage/index" --server "$ZOEKT_SERVER_BIN" --audit "$dy_audit"
(
    cd -- "$dy_stage/index"
    sha256sum -- *.zoekt > SHA256SUMS
)
jq -n --argjson count "$(jq length "$dy_audit/included-files.json")" \
    --arg binary_sha256 "$(sha256sum "$ZOEKT_SERVER_BIN" | cut -d ' ' -f 1)" \
    '{status:"verified",format:1,source_files:$count,server_sha256:$binary_sha256,storage_encoding:"UTF-8"}' \
    > "$dy_stage/index/bundle.json"
mkdir -p -- "$(dirname -- "$dy_output")"
if [[ -e "$dy_output" ]]; then
    dy_backup="${dy_output}.previous-$(date -u +%Y%m%dT%H%M%SZ)"
    test ! -e "$dy_backup"
    mv -- "$dy_output" "$dy_backup"
    printf '旧索引已保留：%s\n' "$dy_backup"
fi
mv -- "$dy_stage/index" "$dy_output"
printf '索引已通过全文件回读：%s\n审计：%s\n' "$dy_output" "$dy_audit"
