<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=e4bf1235c8a582b596af733bb2e5896ecc401751 model=claude-sonnet-5-5 -->
> このページは[英語の原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)の機械翻訳です。内容は英語の原文が優先されます。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# コーディングエージェント向けのコードに紐づくメモリ

Loom は、コーディングエージェントのメモリを、そのメモリが名指しする関数、クラス、ファイルに紐づけ、リファクタリングをまたいで追跡し、対応するコードが変更されるとメモリにフラグを立てます。デフォルトでは、コードがすべて失われたメモリは検索結果から除外され、教訓を返すすべての読み取りは、そのコードが変更されたか、失われたかを知らせます。この機能は、セルフホストの Loom がインデックスしたリポジトリ（[コードインデックス](code-intelligence.md)、`LOOM_CODE_INGEST_ENABLED=true`）に対して MCP、HTTP、SDK 経由で動作し、リンクを維持する `memory_code_links` ワーカーはデフォルトで有効です。

<a id="what-you-get"></a>

## 提供される機能

- **対象のコードに紐づく教訓。** `repo:<id>` タグ付きのメモリ（Claude Code と Codex のプラグインは、フックで取得した内容にこのタグを付けます）、またはリポジトリのコード名前空間に書かれたメモリは、次のものにリンクされます。バッククォートで名指しされた定義（関数、クラスなどのシンボル）、または本文中のどこかで `A::b` や呼び出し（`a.b()`、`name()`）の形で書かれ、一意の場所に解決される定義。メモリが列挙するファイル内の定義のうち、camelCase または snake_case の単語だけで名指しされたもの。そして、列挙されたファイルのうち上記の定義をひとつも含まないファイル。まだトランクにない修飾名は、`LOOM_MEMORY_CODE_LINKS_PENDING_DAYS`（14日）以内にマージされればリンクされます。
  [コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **リファクタリングに追従するリンク。** インデックスされるコミットごとに、各リンクは `fresh`、`moved`、`changed`（変更したコミットを示す）、`unresolved`、`missing` のいずれかになります。リンクは定義のトークンを記録するため、空白、コメント、ほとんどのフォーマッタによる変更では `fresh` のままです。[リンクの状態](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **古くなったメモリを選んだ方法で処理。** 名前空間の [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) が決めます。`retire_removed`（デフォルト）は、コードがすべて失われたメモリを検索から除外し、変更されたコードにフラグを立てます。`flag` はフラグを立てるだけです。`revise` はコードが失われたメモリを同様に除外し、さらに変更されたメモリの主張をモデルが旧コードと新コードに照らして確認し、修正案を提示します。どのポリシーでも、コードが変更されたメモリは、人が保持していない限り、隠されずに検索結果の次の3件より下に順位付けされます。[ポリシー](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code) Loom がメモリ自体を書き換えることはありません。
- **エージェントがコードを読むあらゆる場面で教訓を提示。** `code.symbol` は定義に紐づくメモリを `lessons` として返します。`code.search`、`code.callers`、`code.callees`、`code.impact`、`code.outline`、およびタスクの `code.context_pack` は、返すファイルの教訓を付加します。各教訓にはリンクの状態が付きます。Claude Code と Codex のフックは、エージェントがファイルを編集する前にそのファイルのメモリ（`memory.for_file`）を表示し、Cline のフックはファイルの読み取り時や編集時に表示します。[`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Claude Code プラグイン](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md) ·
  [Codex プラグイン](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/codex.md)
- **チームで共有。** 教訓は、リポジトリのコード名前空間と、呼び出しが指定する名前空間（MCP ではデフォルトで呼び出し元自身の名前空間）から読み取られます。そのため、リポジトリのコード名前空間に保存された教訓は、その名前空間を読めるすべてのエージェントに届きます。
  [コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **ブランチごとに自身のコードを参照。** 別のブランチでは、教訓はそのブランチ自身の定義と照合されるため、古いテキストを保持しているリリースブランチでは `fresh` と表示されます。
  [コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **コードと並ぶドキュメント。** リポジトリのコード名前空間（そのトランク）に登録されたソースのドキュメントにはリポジトリのタグが付くため、リポジトリに対する検索でコードと一緒に読み取られます（同期ワーカーには `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED` が必要です）。
  [ドキュメントソース](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **レビュー用リスト。** [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) は、コードが変更された、再び見つからない、または失われたメモリを一覧し、ダッシュボードは各メモリのリンクとその状態を表示します。

![ダッシュボードでのメモリの詳細：リンクされたコード。1つの定義は最新で、もう1つはあるコミット以降に変更されている](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## 開始する

[クイックスタート](../getting-started/quickstart.md)で Loom を起動し、[リポジトリの追加](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)でリポジトリのコードインデックスを有効にします。コーディングエージェントを、プラグインまたは MCP で接続します（[コーディングエージェントの接続](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）。4文字以上の定義をバッククォートで名指しするか、`` `Reader.run` `` のような修飾名を使った教訓を保存します。Claude Code と Codex のプラグインは、フックで取得した内容に `repo:<id>` を付け、セッションの primer が id を示します（[導出方法](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)）。エージェントが `loom_memory_set` で自分で保存した教訓は、そのタグ（`tags: ["repo:<id>"]`）を持つか、リポジトリのコード名前空間に書かれていればリンクされます。どのクライアントでもどちらも可能です。HTTP（または Python SDK の `agent.call`）では、自分の名前空間に保存された教訓は、呼び出しが `lesson_namespaces` でその名前空間を指定した場合にのみ読み取られます。各リンクパスは作成から60秒以上経過した書き込みをリンクし、パスは60秒ごとに実行されるため、トランクがインデックスされれば、その定義に対する `code.symbol` は約2分以内に `lessons` の下に教訓を返します。

`code.callees` と `code.outline` は実験的です（[ステータス](https://github.com/ClickHouse/loom/blob/main/docs/reference/status.md)）。このページのその他の部分は安定版です。
