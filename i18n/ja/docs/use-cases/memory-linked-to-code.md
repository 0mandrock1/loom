<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=ba0db8ea1cf39d2ef1aa0c782706183107d553a7 model=claude-sonnet-5-5 -->
> このページは[英語の原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)の機械翻訳です。内容は英語の原文が優先されます。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# コーディングエージェント向けのコードにリンクされたメモリ

Loom は、コーディングエージェントがリポジトリについて保存したメモリを、そのメモリが名指しする関数、クラス、ファイルにリンクします。リファクタリングをまたいでそれらのシンボルを追跡し、対応するコードが変更されるとメモリにフラグを立てます。デフォルトでは、紐づくコードがすべて消えたメモリは検索から外れるため、古くなった教訓がエージェントに届かなくなります。この機能は Loom がインデックスしているリポジトリ（[コードインデックス](code-intelligence.md)、`LOOM_CODE_INGEST_ENABLED=true`）で動作し、リンクを維持する `memory_code_links` ワーカーはデフォルトで有効です。

<a id="what-you-get"></a>

## 得られるもの

- **教訓を、その対象のコードに結びつけます。** `repo:<id>` タグが付いたメモリ（Claude Code と Codex のプラグインは、フックが取得した内容にこのタグを付けます）、またはリポジトリのコード名前空間に書かれたメモリは、次の対象にリンクされます。バッククォートで名指しされ、1 か所にだけ解決される各定義。メモリが列挙するファイル内にあり、camelCase または snake_case の単語だけで名指しされた定義。そして、それらの定義を一つも含まないまま名指しされた各ファイル。trunk にまだ存在しない修飾名は、`memory_code_links_pending_days`（14）以内にマージされればリンクされます。
  [コードにリンクされたメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **リファクタリングに追従するリンク。** インデックスされるコミットごとに、各リンクの状態は `fresh`、`moved`、`changed`（変更したコミットを示します）、`unresolved`、`missing` のいずれかになります。リンクは定義のトークンを記録するため、空白、コメント、ほとんどのフォーマッタによる変更では `fresh` のままです。
- **古くなったメモリを、選んだ方法で扱います。** 名前空間の [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md) が方法を決めます。`retire_removed`（デフォルト）は、コードがすべて消えたメモリを検索から外し、変更されたコードにはフラグを立てます。`flag` はフラグを立てるだけです。`revise` はコードが消えたメモリを同様に検索から外し、さらに変更されたメモリについて、モデルが旧コードと新コードに照らしてその主張を確認し、修正案を提示します。どのポリシーでも、コードが変更されたメモリは非表示にはならず、検索では次の 3 件の結果より下に順位づけされます。Loom がメモリ自体を書き換えることはありません。
- **エージェントがコードを読む場面ごとに教訓を表示します。** `code.symbol` は定義にリンクされたメモリを `lessons` として返します。`code.search`、`code.callers`、`code.callees`、`code.impact`、`code.outline`、`code.context_pack` は、返すファイルに対応する教訓を付加します。各教訓にはリンクの状態が付きます。Claude Code と Codex のフックは、エージェントがファイルを編集する前にそのファイルのメモリ（`memory.for_file`）を表示し、Cline のフックは、ファイルを読むとき、または編集するときに表示します。
  [`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Claude Code プラグイン](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **チームで共有されます。** 教訓はリポジトリのチーム名前空間と、デフォルトでは呼び出し元自身の名前空間から読み取られます。そのため、あるエージェントがそこに保存した教訓は、そのコードを読むすべてのエージェントに届きます。
- **ブランチごとに自身のコードを読みます。** 別のブランチでは、教訓はそのブランチ自身の定義に照らして検査されます。そのため、古いテキストをまだ持つリリースブランチでは `fresh` と判定されます。
- **コードと並ぶドキュメント。** リポジトリの trunk コード名前空間に登録されたソースのドキュメントにはリポジトリのタグが付くため、リポジトリを対象とした検索でコードと一緒に読み取られます（同期ワーカーには `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED` が必要です）。
  [ドキュメントソース](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **レビュー用の一覧。**
  [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) は、コードが変更された、または消えたメモリを一覧表示します。ダッシュボードには、各メモリのリンクとその状態が表示されます。

![ダッシュボードのメモリ詳細：リンクされたコード。一つの定義は最新で、もう一つはあるコミット以降に変更されている](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## はじめに

[クイックスタート](../getting-started/quickstart.md)で Loom を起動し、[リポジトリの追加](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)でリポジトリのコードインデックスを有効にします。コーディングエージェントをプラグインまたは MCP で接続します（[コーディングエージェントの接続](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）。4 文字以上の定義（または `` `Reader.run` `` のような修飾名）をバッククォートで名指しした教訓を、`repo:<id>` タグ付きで、またはリポジトリのコード名前空間に書いて保存します。trunk がインデックスされ、保存の 1 分後にリンクのパスが実行されると（パスは 60 秒ごとに実行され、最新の書き込みから 60 秒遅れるため、約 2 分以内）、その定義に対する `code.symbol` が、その教訓を `lessons` の下に返します。
