#!/usr/bin/env python3
"""WordPress Rakuten online loader. Python 3.8+, standard library only."""
import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE_URL = 'https://wordpress-rakuten-rules-8d8ab.hallelujah097hallelujah.workers.dev'
SERVICE = 'wordpress-rakuten-editor'
LOADER_VERSION = '1.1.0'
MAX_BYTES = 8 * 1024 * 1024
MAX_FILES = 150
# A snapshot stays readable this long after its last use, so a request that
# is already underway is never pulled out from under a newer version.
SNAPSHOT_GRACE_SECONDS = 24 * 60 * 60
GUIDES = {'START-HERE.html', 'CARD-CONVERSION.html', 'IMAGE-RULES.html', 'MULTI-SITE.html',
          'CSS-SETUP.html', 'RAKUTEN-MARKET.html', 'RAKUTEN-TRAVEL.html', 'RAKUTEN-FURUSATO.html',
          'AMAZON-SETUP.html', 'ONLINE-UPDATE.html'}
REQUIRED = {'SKILL.md', 'references/multiple-sites.md', 'references/auto-routing.md',
            'references/product-display-name.md', 'references/legacy-managed-cards.md',
            'references/source-link-migration.md', 'scripts/local_config.py',
            'scripts/amazon_links.py', 'assets/swl-rkpc.html', 'assets/swl-rkpc.css',
            'assets/rthc9/rthc9-hotel-card.html', 'assets/ftc9/ftc9-furusato-card.html'}
ENTRY = '.agents/skills/wordpress-rakuten-editor/'
BOOT_REQUIRED = {ENTRY+'SKILL.md', ENTRY+'scripts/fetch_rules.py', ENTRY+'agents/openai.yaml',
                 'AGENTS.md', '.gitignore', 'START-HERE.html', 'ONLINE-UPDATE.html'}
START_MARKER = '<!-- wordpress-rakuten-online:start -->'
END_MARKER = '<!-- wordpress-rakuten-online:end -->'


class FetchError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FetchError('配布元が転送を要求しました。取得先・公開設定を確認してください。')


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FetchError('配布JSONに重複した項目があります。')
        result[key] = value
    return result


def _json(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise FetchError('配布データのサイズが不正です。')
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=_object)
    except (ValueError, UnicodeError, RecursionError):
        raise FetchError('配布データが正しいUTF-8 JSONではありません。') from None
    if not isinstance(result, dict):
        raise FetchError('配布データの形式が不正です。')
    return result


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}', value):
        raise FetchError('配布バージョンの形式が不正です。')
    return tuple(map(int, value.split('.')))


def read_url(url):
    origin, target = urlsplit(BASE_URL), urlsplit(url)
    if origin.scheme != 'https' or target.scheme != 'https' or target.netloc != origin.netloc or target.username or target.password or target.fragment:
        raise FetchError('設定されたHTTPS配布元以外への通信を停止しました。')
    request = Request(url, headers={'Cache-Control': 'no-cache, no-store',
        'Accept': 'application/json', 'Accept-Encoding': 'identity',
        'User-Agent': 'WordPress-Rakuten-Editor-Live/'+LOADER_VERSION})
    try:
        with build_opener(NoRedirect()).open(request, timeout=25) as response:
            if response.status != 200 or response.headers.get_content_type() != 'application/json':
                raise FetchError('配布元がJSONを返しません。ログイン画面・公開設定を確認してください。')
            raw = response.read(MAX_BYTES+1)
            if len(raw) > MAX_BYTES:
                raise FetchError('配布データが上限サイズを超えています。')
            return raw
    except HTTPError as exc:
        raise FetchError('配布元のHTTPエラー: {}'.format(exc.code)) from None
    except (URLError, TimeoutError, OSError):
        # Agent sandboxes block outbound traffic by default, and the previous
        # wording let that look like a broken distribution: the agent reported
        # the failure instead of re-running the command with network access.
        raise FetchError('配布元へ接続できません。この取得にはインターネット通信が必要です。'
                         'エージェントで実行している場合は、ネットワークを許可して同じコマンドを'
                         '実行し直してください。許可を求められたら利用者に承認を依頼してください。'
                         ) from None


def _safe_path(path, kind):
    if not isinstance(path, str) or not path or len(path) > 180 or '\\' in path or ':' in path:
        return False
    if any(p in ('', '.', '..') for p in path.split('/')):
        return False
    if kind == 'bootstrap':
        if path in BOOT_REQUIRED or path in {ENTRY+'assets/icon.svg', 'sites.example.json', '.env.example'}:
            return True
    elif path == 'SKILL.md' or re.fullmatch(r'references/[a-z0-9-]+\.md|scripts/[a-z0-9_]+\.(?:py|mjs|js)', path):
        return True
    return path in GUIDES or bool(re.fullmatch(r'assets/(?:[a-z0-9-]+/)?[a-z0-9-]+\.(?:html|css|js|md|svg)', path))


def fetch_release(kind='rules', transport=None):
    if kind not in ('rules', 'bootstrap'):
        raise FetchError('取得種別が不正です。')
    transport = transport or read_url
    manifest_url = BASE_URL + '/' + kind + '/latest.json?request=' + uuid.uuid4().hex
    manifest = _json(transport(manifest_url))
    if type(manifest.get('protocol')) is not int or manifest['protocol'] != 1 or manifest.get('service') != SERVICE:
        raise FetchError('配布元または取得方式が対応していません。')
    if _version(manifest.get('min_loader')) > _version(LOADER_VERSION):
        raise FetchError('入口の更新が必要です。導入案内からオンライン版を更新してください。')
    _version(manifest.get('version'))
    rev = manifest.get('revision')
    size = manifest.get('bytes')
    if not isinstance(rev, str) or not re.fullmatch(r'[a-f0-9]{64}', rev) or type(size) is not int or not 0 < size <= MAX_BYTES:
        raise FetchError('配布データの識別子・サイズが不正です。')
    expected = '/'+kind+'/releases/'+rev+'/bundle.json'
    if manifest.get('bundle_path') != expected or manifest.get('sha256') != rev:
        raise FetchError('配布データの取得先が不正です。')
    raw = transport(BASE_URL+expected)
    if not isinstance(raw, bytes) or len(raw) != size or hashlib.sha256(raw).hexdigest() != rev:
        raise FetchError('取得したファイルのサイズまたはハッシュが一致しません。')
    bundle = _json(raw)
    if bundle.get('service') != SERVICE or type(bundle.get('protocol')) is not int or bundle['protocol'] != 1 or bundle.get('kind') != kind or bundle.get('version') != manifest['version']:
        raise FetchError('配布ルールの版が一致しません。')
    files = bundle.get('files')
    required = REQUIRED if kind == 'rules' else BOOT_REQUIRED
    if not isinstance(files, dict) or not len(required) <= len(files) <= MAX_FILES or not required.issubset(files):
        raise FetchError('必要なファイルが不足しています。')
    folded = set()
    for name, content in files.items():
        if not _safe_path(name, kind) or not isinstance(content, str) or '\x00' in content or name.casefold() in folded:
            raise FetchError('配布ファイルの名前・内容が不正です。')
        folded.add(name.casefold())
    if kind == 'rules':
        for name, content in files.items():
            if not name.endswith('.md'):
                continue
            for link in re.findall(r'\]\(([^)]+)\)', content):
                if '://' in link or link.startswith('#'):
                    continue
                target = posixpath.normpath(posixpath.join(posixpath.dirname(name), link.split('#')[0]))
                if target.startswith('../') or target not in files:
                    raise FetchError('ルールが参照する資料が不足しています。')
    return files, {'ok': True, 'version': manifest['version'], 'revision': rev,
        'fetched_at': datetime.now(timezone.utc).isoformat(), 'manifest_url': manifest_url,
        'file_count': len(files), 'loader_version': LOADER_VERSION}


def _target(root, rel):
    p = root / rel
    for part in [p, *p.parents]:
        if part == root:
            break
        if part.is_symlink():
            raise FetchError('導入先にシンボリックリンクがあります。対象を確認してください。')
    return p


def fetch_rules(project_dir, transport=None):
    files, receipt = fetch_release('rules', transport)
    project = Path(project_dir).resolve()
    if not project.is_dir():
        raise FetchError('元の作業フォルダを指定してください。')
    parent = _target(project, '.rakuten-rules')
    parent.mkdir(exist_ok=True)
    # Name the snapshot after the verified revision so repeated requests for the
    # same rules reuse one folder. Previously every call left another verified-*
    # directory behind, so SKILL.md and references resolved to several copies.
    short = re.sub(r'[^0-9a-f]', '', str(receipt.get('revision', '')).lower())[:12]
    if len(short) < 12:
        raise FetchError('配布元のリビジョン表記を確認できません。')
    final = parent / ('verified-' + short)
    root = Path(tempfile.mkdtemp(prefix='.staging-', dir=parent))
    try:
        for rel, content in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode('utf-8'))
        receipt.update(snapshot_dir=str(final), project_dir=str(project),
            config_path=str(project / ('sites.local.json' if (project/'sites.local.json').exists() else '.env.local')))
        (root/'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        if final.is_dir():
            shutil.rmtree(final)
        root.rename(final)
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    _prune_snapshots(project, parent, final)
    return receipt


def _prune_snapshots(project, parent, keep):
    """Drop snapshots nothing can still be using.

    A request already underway keeps reading the snapshot it fixed, so a newer
    version must not delete it. Three things are kept: the snapshot just
    written, any snapshot a Fleet rules-lock.json pins, and any snapshot
    touched inside SNAPSHOT_GRACE_SECONDS. An unreadable lock prunes nothing at
    all. Same-revision requests reuse one directory, so in steady state this
    leaves exactly one.
    """
    wanted = {keep.resolve()}
    for lock in project.glob('jobs/*/*/*/rules-lock.json'):
        try:
            wanted.add(Path(json.loads(lock.read_text(encoding='utf-8'))['snapshot_dir']).resolve())
        except Exception:
            return
    fresh = time.time() - SNAPSHOT_GRACE_SECONDS
    for stale in parent.iterdir():
        if not stale.is_dir() or stale.is_symlink():
            continue
        if stale.name.startswith('.staging-'):
            shutil.rmtree(stale, ignore_errors=True)
            continue
        if not stale.name.startswith('verified-') or stale.resolve() in wanted:
            continue
        try:
            if stale.stat().st_mtime >= fresh:
                continue
        except OSError:
            continue
        shutil.rmtree(stale, ignore_errors=True)


def install(project_dir, transport=None):
    files, receipt = fetch_release('bootstrap', transport)
    project = Path(project_dir).resolve()
    project.mkdir(parents=True, exist_ok=True)
    # Check all destination paths before changing any file; credentials are not permitted targets.
    for rel in files:
        target = _target(project, rel)
        if target.exists() and not target.is_file():
            raise FetchError('導入先のファイル名に同名のフォルダがあります。')
    updates = {rel: text.encode('utf-8') for rel, text in files.items()}
    agents = project/'AGENTS.md'
    if agents.exists():
        original = agents.read_text(encoding='utf-8')
        if START_MARKER in original or END_MARKER in original:
            if original.count(START_MARKER) != 1 or original.count(END_MARKER) != 1 or original.index(START_MARKER) >= original.index(END_MARKER):
                raise FetchError('AGENTS.mdのオンライン管理区間が不正です。')
            original = original[:original.index(START_MARKER)] + original[original.index(END_MARKER)+len(END_MARKER):]
        # Retain personal instructions, append the current scoped online entry.
        updates['AGENTS.md'] = (original.rstrip()+'\n\n'+files['AGENTS.md']).encode('utf-8')
    ignore = project/'.gitignore'
    if ignore.exists():
        old = ignore.read_text(encoding='utf-8')
        new_lines = [line for line in files['.gitignore'].splitlines() if line and line not in old.splitlines()]
        updates['.gitignore'] = (old.rstrip()+'\n'+'\n'.join(new_lines)+'\n').encode('utf-8')
    prior = {rel: (project/rel).read_bytes() if (project/rel).exists() else None for rel in updates}
    backup = _target(project, 'backups/skill-install/'+uuid.uuid4().hex)
    backup.mkdir(parents=True)
    for rel, old in prior.items():
        if old is not None:
            p = backup/rel;p.parent.mkdir(parents=True, exist_ok=True);p.write_bytes(old)
    changed = []
    try:
        for rel, data in updates.items():
            path = project/rel;path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix='.online-install-', dir=path.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                os.replace(tmp, path)
                changed.append(rel)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
    except Exception:
        for rel in reversed(changed):
            if prior[rel] is None:
                (project/rel).unlink()
            else:
                (project/rel).write_bytes(prior[rel])
        raise FetchError('導入に失敗したため変更済みファイルを元へ戻しました。') from None
    receipt.update(project_dir=str(project), backup_dir=str(backup), installed=True)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('fetch', 'install'))
    # Default to the folder Codex opened; the agent no longer has to discover a path.
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    try:
        result = (fetch_rules if args.action == 'fetch' else install)(args.project_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except FetchError as exc:
        error = str(exc)
    except Exception:
        error = 'ファイル処理を完了できませんでした。権限・空き容量を確認してください。'
    print(json.dumps({'ok':False,'error':error,'action':'最新版の取得に失敗しました。旧版で記事更新を続けないでください。'},ensure_ascii=False),file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
