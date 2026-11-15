<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=020733a9f887152df7c768465027827db57128ba model=claude-sonnet-5-5 -->
> このページは[英語の原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)の機械翻訳です。内容は英語の原文が優先されます。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# コーディングエージェント向けのコードに紐づくメモリ

Loom は、コーディングエージェントのメモリを、そのメモリが名指しする関数、クラス、ファイルに紐づけ、リファクタリングをまたいで追跡し、コードが変更されるとメモリにフラグを立てます。デフォルトでは、紐づくコードがすべて消えたメモリを検索から除外し、教訓を返すすべての読み取りは、そのコードが移動したか、変更されたか、消えたかを示します。これは、セルフホストの Loom がインデックス化したリポジトリ（[コードインデックス](code-intelligence.md)、`LOOM_CODE_INGEST_ENABLED=true`）に対して MCP、HTTP、SDK 経由で動作し、リンクを維持する `memory_code_links` ワーカーはデフォルトで有効です。

<a id="what-you-get"></a>

## 得られるもの

- **教訓を、その対象のコードに紐づけます。** `repo:<id>` のタグが付いたメモリ（Claude Code と Codex のプラグインは、フックが取得した内容にタグを付けます）、またはリポジトリのコード名前空間に書かれたメモリは、バッククォートで名指しされた定義、または本文中のどこかに `A::b` や呼び出し（`a.b()`、`name()`）として現れ、ちょうど 1 か所に解決される定義に紐づきます。また、メモリが列挙するファイル内の定義のうち、素の camelCase または snake_case の単語で名指しされたものに紐づき、列挙されたファイルのうち、それらの定義を1つも含まないファイルにも紐づきます。まだ trunk にない修飾名は、`LOOM_MEMORY_CODE_LINKS_PENDING_DAYS`（14）日以内にマージされればリンクされます。[コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **リファクタリングに追従するリンク。** インデックス化されたコミットごとに、各リンクは `fresh`、`moved`、`changed`（変更したコミットを示します）、`unresolved`、`missing` のいずれかを示します。リンクは定義のトークンを記録するため、空白、コメント、ほとんどのフォーマッタによる変更では `fresh` のままです。[リンクの状態](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **古くなったメモリを、選んだ方法で扱います。** 名前空間の [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) が決めます。`retire_removed`（デフォルト）は、コードがすべて消えたメモリを検索から除外し、変更されたコードにフラグを立てます。`flag` はフラグを立てるだけです。`revise` はコードが消えたメモリも除外し、さらに変更されたメモリについて、モデルが旧コードと新コードに照らしてその主張を確認し、修正案を提示します。どのポリシーでも、検索はコードが変更されたメモリを隠さず、次の 3 件の結果より下に順位付けします。Loom がメモリ自体を書き換えることはありません。
- **エージェントがコードを読むあらゆる場面で教訓を表示します。** `code.symbol` は、定義に紐づくメモリを `lessons` として返します。`code.search`、`code.callers`、`code.callees`、`code.impact`、`code.outline`、およびタスクの `code.context_pack` は、返すファイルの教訓を付加します。各教訓にはリンクの状態が付きます。Claude Code と Codex のフックは、エージェントが編集する前にファイルのメモリ（`memory.for_file`）を表示し、Cline のフックはファイルを読むとき、または編集するときに表示します。[`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) · [Claude Code プラグイン](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **チームで共有できます。** 教訓は、リポジトリのコード名前空間と、呼び出しで指定した名前空間から読み取られます（MCP では、デフォルトで呼び出し元自身の名前空間です）。そのため、リポジトリのコード名前空間に保存された教訓は、そのコードを読むすべてのエージェントに届きます。[コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **ブランチごとに自分のコードを読みます。** 別のブランチでは、教訓はそのブランチ自身の定義と照合されるため、古いテキストを残しているリリースブランチでは `fresh` と表示されます。[コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **コードの隣にあるドキュメント。** リポジトリのコード名前空間（trunk のもの）に登録されたソースのドキュメントにはリポジトリのタグが付くため、リポジトリに対する検索でコードと一緒に読み取られます（同期ワーカーには `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED` が必要です）。[ドキュメントソース](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **レビュー用リスト。** [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) は、コードが変更された、再び見つからなかった、または消えたメモリを一覧し、ダッシュボードは各メモリのリンクとその状態を表示します。

![ダッシュボードのメモリ詳細：紐づくコード。1 つの定義は最新で、もう 1 つはあるコミット以降に変更されている](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## 始める

[クイックスタート](../getting-started/quickstart.md)で Loom を起動し、[リポジトリの追加](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)でリポジトリのコードインデックスを有効にします。コーディングエージェントをプラグインまたは MCP で接続します（[コーディングエージェントの接続](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）。バッククォートで 4 文字以上の定義名、または `` `Reader.run` `` のような修飾名を名指しする教訓を保存します。Claude Code と Codex のプラグインは、フックが取得した内容に `repo:<id>` のタグを付け、セッションのプライマーが id を示します（[導出方法](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)）。エージェントが `loom_memory_set` で自ら保存した教訓は、そのタグ（`tags: ["repo:<id>"]`）を持つか、リポジトリのコード名前空間に書かれていればリンクされます。どのクライアントでも、どちらも可能です。HTTP や SDK では、自分の名前空間に保存した教訓は、呼び出しがその名前空間を `lesson_namespaces` で指定した場合にのみ読み取られます。各リンクパスは 60 秒以上前の書き込みをリンクし、パスは 60 秒ごとに実行されるため、trunk がインデックス化されると、その定義に対する `code.symbol` は約 2 分以内に `lessons` の下に教訓を返します。

このページの一部の op は、[ステータスページ](https://github.com/ClickHouse/loom/blob/main/docs/reference/status.md)で experimental と記されています。
