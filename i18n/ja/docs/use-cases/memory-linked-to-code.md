<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=b9e9c3f0bf6d83928302d688a3f5c2bb304521db model=claude-sonnet-5-5 -->
> このページは[英語の原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)の機械翻訳です。内容は英語の原文が優先されます。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# コーディングエージェント向けのコードにリンクされたメモリ

Loom は、コーディングエージェントがリポジトリについて保存したメモリを、そのメモリが名指しする関数、クラス、ファイルにリンクし、リファクタリングを経てもそれらのシンボルを追跡し、コードが変更されるとメモリにフラグを立てます。デフォルトでは、対応するコードがすべて消えたメモリは検索結果から外れ、教訓を表示するそのほかの読み取りでは、そのコードが移動したか、変更されたか、消えたかが示されます。この機能は、セルフホストの Loom がインデックスしたリポジトリ（[コードインデックス](code-intelligence.md)、`LOOM_CODE_INGEST_ENABLED=true`）に対して MCP 経由で動作し、リンクを維持する `memory_code_links` ワーカーはデフォルトで有効です。

<a id="what-you-get"></a>

## 得られるもの

- **対象のコードに紐付いた教訓。** `repo:<id>` タグが付いたメモリ（Claude Code と Codex のプラグインは、フックが取得した内容にこのタグを付けます）、またはリポジトリのコード名前空間に書かれたメモリは、バッククォートで名指しされた定義にリンクされます。名指しの形は `A::b` または呼び出し（`a.b()`）で、ちょうど 1 か所に解決されるものが対象です。メモリが列挙するファイル内の定義で、camelCase または snake_case の単語だけで名指しされたものにもリンクされ、それらの定義を含まない名指しのファイルにもリンクされます。まだトランクにない修飾名は、`memory_code_links_pending_days`（14）以内にマージされればリンクされます。
  [コードにリンクされたメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **リファクタリングに追従するリンク。** インデックスされたコミットごとに、各リンクは `fresh`、`moved`、`changed`（変更したコミットが示されます）、`unresolved`、`missing` のいずれかになります。リンクは定義のトークンを記録するため、空白、コメント、ほとんどのフォーマッタによる変更では `fresh` のままです。[リンクの状態](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **古くなったメモリを好みの方法で扱えます。** 名前空間の [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) が扱いを決めます。`retire_removed`（デフォルト）は、コードがすべて消えたメモリを検索から外し、変更されたコードにフラグを立てます。`flag` はフラグを立てるだけです。`revise` はコードが消えたメモリも同様に外し、さらに変更されたメモリについて、モデルが旧コードと新コードに照らして主張を確認し、修正した主張を提案します。どのポリシーでも、検索はコードが変更されたメモリを隠さず、次の 3 件の結果より下に順位付けします。Loom がメモリ自体を書き換えることはありません。
- **エージェントがコードを読むあらゆる場面での教訓。** `code.symbol` は、定義にリンクされたメモリを `lessons` として返します。`code.search`、`code.callers`、`code.callees`、`code.impact`、`code.outline`、`code.context_pack` は、返すファイルの教訓を付加します。各教訓にはリンクの状態が付きます。Claude Code と Codex のフックは、エージェントがファイルを編集する前にそのファイルのメモリ（`memory.for_file`）を表示し、Cline のフックはファイルを読むときや編集するときに表示します。[`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ・
  [Claude Code プラグイン](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **チームで共有。** 教訓は、リポジトリのコード名前空間と、呼び出しが名指しする名前空間から読み取られます（MCP 経由では、デフォルトで呼び出し元自身の名前空間）。そのため、リポジトリのコード名前空間に保存された教訓は、そのコードを読むすべてのエージェントに届きます。
  [コードにリンクされたメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **ブランチごとに自身のコードを読む。** 別のブランチでは、教訓はそのブランチ自身の定義に照らして確認されるため、古いテキストをまだ持つリリースブランチでは `fresh` と読まれます。
  [コードにリンクされたメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **コードと並ぶドキュメント。** リポジトリのコード名前空間（そのトランクのもの）に登録されたソースのドキュメントにはリポジトリのタグが付くため、リポジトリに対する検索でそのコードと一緒に読まれます（同期ワーカーには `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED` が必要です）。
  [ドキュメントソース](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **レビュー用リスト。** [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) は、コードが変更された、または消えたメモリを一覧表示し、ダッシュボードは各メモリのリンクとその状態を表示します。

![ダッシュボードにおけるメモリの詳細：リンクされたコード。1 つの定義は最新で、もう 1 つはコミット以降に変更されている](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## はじめに

[クイックスタート](../getting-started/quickstart.md)で Loom を起動し、[リポジトリの追加](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)でリポジトリのコードインデックスを有効にします。コーディングエージェントは、プラグインまたは MCP で接続します（[コーディングエージェントの接続](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）。4 文字以上の定義、または `` `Reader.run` `` のような修飾名をバッククォートで名指しした教訓を保存します。Claude Code と Codex のプラグインは `repo:<id>` タグを自動的に付けます（[id の導出方法](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)）。それ以外のクライアントからは、リポジトリのコード名前空間に教訓を書いてください。各リンクパスは、作成から 60 秒以上経過した書き込みをリンクし、パスは 60 秒ごとに実行されるため、トランクがインデックスされれば、その定義に対する `code.symbol` は約 2 分以内に `lessons` の下にその教訓を返します。
