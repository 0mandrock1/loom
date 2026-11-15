<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=248230780bfc4a7af42bcb776c9574c89ec21839 model=claude-sonnet-5-5 -->
> このページは[英語の原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)の機械翻訳です。内容は英語の原文が優先されます。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# コーディングエージェント向けのコードにリンクされたメモリ

Loom は、コーディングエージェントがリポジトリについて保存したメモリを、そのメモリが名指しする関数、クラス、ファイルにリンクし、リファクタリングを越えてそれらのシンボルを追跡し、コードが変更されるとメモリにフラグを付けます。デフォルトでは、対応するコードがすべて消えたメモリは検索結果から外れ、教訓を表示するそれ以外の読み取りでも、そのコードが移動したか、変更されたか、消えたかが示されます。この機能は、セルフホストの Loom がインデックスするリポジトリ（[コードインデックス](code-intelligence.md)、`LOOM_CODE_INGEST_ENABLED=true`）に対して MCP、HTTP、SDK 経由で動作し、リンクを維持する `memory_code_links` ワーカーはデフォルトで有効です。

<a id="what-you-get"></a>

## できること

- **教訓を対象のコードに結び付けます。** `repo:<id>` タグ付きのメモリ（Claude Code と Codex のプラグインは、フックが取得した内容にこのタグを付けます）、またはリポジトリのコード名前空間に書かれたメモリは、バッククォートで囲んで `A::b` や呼び出し（`a.b()`）の形で名指しされ、ちょうど 1 か所に解決される各定義にリンクされます。また、メモリが列挙するファイル内の定義のうち、camelCase または snake_case の単語だけで名指しされるもの、そして列挙されたファイルのうちそれらの定義を一つも含まないファイルにもリンクされます。まだ trunk にない修飾名は、`LOOM_MEMORY_CODE_LINKS_PENDING_DAYS`（14）日以内にマージされればリンクされます。
  [コードにリンクされたメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **リファクタリングに追従するリンク。** インデックスされるコミットごとに、各リンクは `fresh`、`moved`、`changed`（変更したコミットを示します）、`unresolved`、`missing` のいずれかになります。リンクは定義のトークンを記録するため、空白、コメント、ほとんどのフォーマッタによる変更では `fresh` のままです。[リンクの状態](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **古くなったメモリを選んだ方法で扱います。** 名前空間の [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) が決めます。`retire_removed`（デフォルト）は、対応するコードがすべて消えたメモリを検索から外し、変更されたコードにフラグを付けます。`flag` はフラグを付けるだけです。`revise` は、コードが消えたメモリも同様に外し、さらに変更されたメモリについてはモデルが旧コードと新コードに照らして主張を確認し、修正案を提示します。どのポリシーでも、検索はコードが変更されたメモリを隠さず、次の 3 件の結果より下に順位付けします。Loom がメモリ自体を書き換えることはありません。
- **エージェントがコードを読むあらゆる場面で教訓を提示します。** `code.symbol` は、定義にリンクされたメモリを `lessons` として返します。`code.search`、`code.callers`、`code.callees`、`code.impact`、`code.outline`、およびタスクの `code.context_pack` は、返すファイルに対する教訓を添付し、各教訓にはリンクの状態が付きます。Claude Code と Codex のフックは、エージェントがファイルを編集する前にそのファイルのメモリ（`memory.for_file`）を表示し、Cline のフックはファイルの読み取り時または編集時に表示します。[`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Claude Code プラグイン](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **チームで共有します。** 教訓は、リポジトリのコード名前空間と、呼び出しが指定する名前空間（MCP ではデフォルトで呼び出し元自身の名前空間）から読み取られるため、リポジトリのコード名前空間に保存された教訓は、そのコードを読むすべてのエージェントに届きます。
  [コードにリンクされたメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **ブランチは自身のコードを読みます。** 別のブランチでは、教訓はそのブランチ自身の定義と照合されるため、古いテキストをまだ持つリリースブランチでは `fresh` と読まれます。
  [コードにリンクされたメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **コードと並ぶドキュメント。** リポジトリのコード名前空間（その trunk の名前空間）に登録されたソースのドキュメントにはリポジトリのタグが付くため、リポジトリに対する検索ではコードと一緒にそれらが読まれます（同期ワーカーには `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED` が必要です）。
  [ドキュメントソース](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **レビュー用リスト。** [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) は、コードが変更された、再び見つからなかった、または消えたメモリを一覧表示し、ダッシュボードは各メモリのリンクとその状態を表示します。

![ダッシュボードのメモリ詳細：リンクされたコード。1 つの定義は最新で、もう 1 つはあるコミット以降に変更されている](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## はじめに

[クイックスタート](../getting-started/quickstart.md)で Loom を起動し、[リポジトリの追加](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)でリポジトリのコードインデックスを有効にします。プラグインまたは MCP でコーディングエージェントを接続します（[コーディングエージェントを接続する](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）。4 文字以上の定義、または `` `Reader.run` `` のような修飾名をバッククォートで囲んで名指しする教訓を保存します。Claude Code と Codex のプラグインは、フックが取得した内容に `repo:<id>` のタグを付け、セッションのプライマーがその id を示します（[導出方法](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)）。エージェントが `loom_memory_set` で自ら保存した教訓は、そのタグ（`tags: ["repo:<id>"]`）を持つか、リポジトリのコード名前空間に書かれていればリンクされます。どのクライアントでもどちらかを行えます。各リンクパスは 60 秒以上前の書き込みをリンクし、パスは 60 秒ごとに実行されるため、trunk がインデックスされれば、その定義に対する `code.symbol` は約 2 分以内にその教訓を `lessons` に返します。
