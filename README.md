# WordPress楽天記事編集（Codexプラグイン）

既存のWordPress記事を、配布元の最新ルールどおりに仕上げるCodexプラグインです。

## 導入（1回だけ）

Codexデスクトップのプラグイン画面から追加できます。コマンドで行う場合は次の2行です。

```bash
codex plugin marketplace add hallelujah097hallelujah-design/wordpress-rakuten-plugin
codex plugin add wordpress-rakuten-editor@yamaken-blog
```

## 使い方

作業用のフォルダをCodexで開き、最初に一度だけ初期設定をします。

```text
初期設定して
```

以後は記事ごとに一言です。

```text
サイトAの1422を仕上げて
```

## 更新について

編集ルールは依頼ごとに配布元から取得するので、**ルールが更新されても導入し直す必要はありません**。
このプラグインの更新が必要になるのは入口そのものが変わるときだけで、その場合は次の1行です。

```bash
codex plugin marketplace upgrade
```

- 配布元: https://wordpress-rakuten-rules-8d8ab.hallelujah097hallelujah.workers.dev
- 入口バージョン: 1.2.0

初期設定の手順と、WordPressアプリケーションパスワード・楽天ウェブサービス・Amazonアソシエイトの取得方法は配布元の案内を参照してください。
