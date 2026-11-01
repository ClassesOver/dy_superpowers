"""项目身份与 Wiki 构建、发布工具。"""
from __future__ import annotations

from pathlib import Path

from ..project_wiki import configure_project, stage_wiki
from ..trace import TraceStore
from ..wiki_sync import sync_wiki
from .runtime import ToolRequestError, append_trace, invalid_arguments


def _handle_project_configure(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    with invalid_arguments(ValueError, PermissionError):
        result = configure_project(workspace, arguments['name'], arguments['code'],
                                   confirmed=arguments.get('confirmed', False))
    return result


def _handle_wiki_build(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    if arguments.get('confirmed') is not True:
        raise ToolRequestError(-32602, '本地 Wiki 构建需用户确认；不会推送远端')
    with invalid_arguments(ValueError, OSError):
        manifest = stage_wiki(workspace)
    return {'built': True, 'project': manifest['project'], 'pages': len(manifest['entries']),
            'overview_slug': manifest['overview_slug'], 'published': False}


def _handle_wiki_sync(name: str, arguments: dict, workspace: Path, store: TraceStore, span_id: str):
    if arguments.get('confirmed') is not True:
        raise ToolRequestError(-32602, '发布 Wiki 需要用户明确确认')
    try:
        result = sync_wiki(workspace, confirmed=True)
    # WikiSyncError 是 ValueError 子类；OSError 覆盖清单页面文件缺失等本地读取失败。
    except (ValueError, OSError, PermissionError) as exc:
        warning = str(exc)
        event = {'event': 'wiki.sync', 'operation': 'sync', 'tool': name,
                 'status': 'warning', 'count': 0, 'result': 'warning',
                 'warning': warning, 'span_id': span_id}
        append_trace(store, arguments['trace_id'], event)
        raise ToolRequestError(-32602, warning) from exc
    event = {'event': 'wiki.sync', 'operation': 'sync', 'tool': name,
             'status': 'ok' if result.get('synced') else 'noop',
             'count': result.get('pages', 0), 'result': 'ok' if result.get('synced') else '未知',
             'span_id': span_id}
    append_trace(store, arguments['trace_id'], event)
    return result


TOOLS = {
    'dy_project_configure': (
        '经用户确认后绑定当前工作区的唯一项目身份（正式项目名称与稳定代号）。'
        '项目未配置或旧配置未经确认时，Wiki 构建与同步都会拒绝执行，不从账套名或文件名猜测。',
        {'type': 'object',
         'required': ['trace_id', 'name', 'code', 'confirmed'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'name': {'type': 'string', 'minLength': 1, 'maxLength': 100,
                                 'description': '经用户确认的正式项目名称，单行文本'},
                        'code': {'type': 'string', 'pattern': '^[a-z0-9]+(?:-[a-z0-9]+)*$', 'maxLength': 32,
                                 'description': '稳定项目代号，小写字母数字连字符'},
                        'confirmed': {'type': 'boolean'}}},
        _handle_project_configure,
    ),
    'dy_wiki_build': (
        '用户确认后按当前项目调研记忆在本地构建中文 Wiki（知识库内项目首页、模块目录、业务主题页）'
        '并生成发布清单；不推送远端、不改动调研记录、不包含 YAML 元数据。',
        {'type': 'object',
         'required': ['trace_id', 'confirmed'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'confirmed': {'type': 'boolean'}}},
        _handle_wiki_build,
    ),
    'dy_wiki_sync': (
        '用户明确确认后把已构建的本项目 Wiki 清单发布到 GitLab Wiki：按页面所有权标记和上次同步哈希校验，'
        '维护共享知识库首页的项目目录；不删除远端页面、不覆盖其他项目页面、不推交付物。'
        '需 DY_WIKI_API_URL / DY_WIKI_TOKEN 及目标项目（DY_WIKI_PROJECT_ID 或 DY_WIKI_GROUP_ID+DY_WIKI_PROJECT_NAME）。',
        {'type': 'object',
         'required': ['trace_id', 'confirmed'],
         'properties': {'trace_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'},
                        'confirmed': {'type': 'boolean'}}},
        _handle_wiki_sync,
    ),
}
