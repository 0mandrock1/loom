<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=28b15a8a4fd05c45dfe8244e3dd6a3e41528f1d2 model=claude-sonnet-5-5 -->
> このページは[英語の原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)の機械翻訳です。内容は英語の原文が優先されます。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# コーディングエージェント向けのコードに紐づくメモリ

Loom は、コーディングエージェントのメモリを、メモリが名指しする関数、クラス、ファイルに紐づけ、リファクタリングをまたいで追跡し、そのコードが変更されるとメモリにフラグを立てます。デフォルトでは、人がピン留め、ロック、検証したメモリを除き、コードがすべて失われたメモリは検索から除外されます。また、教訓を返すすべての読み取りは、そのコードがいつ変更されたか、または失われたことを示します。この機能は、セルフホストの Loom がインデックスするリポジトリ（[コードインデックス](code-intelligence.md)、`LOOM_CODE_INGEST_ENABLED=true`）に対して MCP、HTTP、SDK 経由で動作し、リンクを維持する `memory_code_links` ワーカーはデフォルトで有効です。

<a id="what-you-get"></a>

## 提供される機能

- **対象のコードに紐づく教訓。** `repo:<id>` タグが付いたメモリ（Claude Code と Codex のプラグインは、フックで取得した内容にこのタグを付けます）、またはリポジトリのコード名前空間に書き込まれたメモリは、次の対象にリンクされます。バッククォートで名指しされた定義（関数、クラスなどのシンボル）、または本文中のどこかに `A::b` や呼び出し（`a.b()`、`name()`）の形で現れ、ちょうど 1 か所に解決される定義。メモリが列挙するファイル内にあり、camelCase または snake_case の単語だけで名指しされた定義。そして、列挙されているもののどの定義も含まないファイル。まだトランクにない修飾名は、`LOOM_MEMORY_CODE_LINKS_PENDING_DAYS`（14 日）以内にマージされればリンクされます。
  [コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **リファクタリングに追従するリンク。** インデックスされるコミットごとに、各リンクは `fresh`、`moved`、`changed`（変更したコミットを示す）、`unresolved`、`missing` のいずれかになります。リンクは定義のトークンを記録するため、空白、コメント、ほとんどのフォーマッタによる変更では `fresh` のままです。[リンクの状態](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **古くなったメモリを選んだ方法で処理。** 名前空間の [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) が決めます。`retire_removed`（デフォルト）は、コードがすべて失われたメモリを検索から除外し、変更されたコードにフラグを立てます。`flag` はフラグを立てるだけです。`revise` はコードが失われたメモリを同様に除外し、さらに変更されたメモリについて、モデルが旧コードと新コードに照らして主張を確認し、修正案を提示します。人がピン留め、ロック、検証したメモリが除外されることはありません。どのポリシーでも、コードが変更されたメモリは、隠されるのではなく、通常は検索で次の 3 件の結果より下に順位づけされます。保持されたメモリと、名前で見つかった行は順位が変わりません。Loom がメモリ自体を書き換えることはありません。
  [ポリシー](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **エージェントがコードを読むあらゆる場面で教訓を提供。** `code.symbol` は、定義に紐づくメモリを `lessons` として返します。`code.search`、`code.callers`、`code.callees`、`code.impact`、`code.outline`、およびタスクの `code.context_pack` は、返すファイルに対する教訓を付加します。各教訓はリンクの状態を持ちます。Claude Code と Codex のフックは、エージェントがファイルを編集する前にそのファイルのメモリ（`memory.for_file`）を表示し、Cline のフックはファイルを読むとき、または編集するときに表示します。[`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Claude Code プラグイン](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md) ·
  [Codex プラグイン](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/codex.md)
- **チームでの共有。** 教訓は、リポジトリのコード名前空間と、呼び出しが指定する名前空間から読み取られます（MCP 経由では、デフォルトで呼び出し元自身の名前空間）。そのため、リポジトリのコード名前空間に保存された教訓は、その名前空間を読み取れるすべてのエージェントに届きます（自身のユーザーまたはセッションに限定されたトークンでは、教訓は読み取れません）。
  [コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **ブランチごとに自身のコードを読み取る。** 別のブランチでは、教訓はそのブランチ自身の定義に対して照合されるため、古いテキストを保持しているリリースブランチでは `fresh` と読み取られます。
  [コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **コードと並ぶドキュメント。** リポジトリのコード名前空間（そのトランクのもの）に登録されたソースのドキュメントにはリポジトリのタグが付くため、リポジトリに対する検索でコードとあわせて読み取られます（同期ワーカーには `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED` が必要です）。
  [ドキュメントソース](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **レビュー用リスト。** [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) は、コードが変更された、再び見つからなかった、または失われたメモリを一覧表示し、ダッシュボードは各メモリのリンクとその状態を表示します。

![ダッシュボードのメモリ詳細：リンクされたコード。1 つの定義は最新で、もう 1 つはあるコミット以降に変更されている](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## 開始する

[クイックスタート](../getting-started/quickstart.md)で Loom を起動し、[リポジトリの追加](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)でリポジトリに対してコードインデックスを有効にします。コーディングエージェントを、プラグインまたは MCP で接続します（[コーディングエージェントの接続](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）。4 文字以上の定義を、バッククォートで、または `` `Reader.run` `` のような修飾名で名指しした教訓を保存します。Claude Code と Codex のプラグインは、フックで取得した内容に `repo:<id>` を付け、セッションプライマーが id を示します（[導出方法](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)）。エージェントが `loom_memory_set` で自ら保存した教訓は、このタグ（`tags: ["repo:<id>"]`）を持つか、リポジトリのコード名前空間に書き込まれていればリンクされます。どちらもあらゆるクライアントから行えます。HTTP（または Python SDK の `agent.call`）では、自身の名前空間に保存された教訓は、呼び出しが `lesson_namespaces` でその名前空間を指定した場合にのみ読み取られます。各リンクパスは、書き込みから 60 秒以上経過したものをリンクし、パスは 60 秒ごとに実行されます。そのため、トランクがインデックスされると、その定義に対する `code.symbol` は、約 2 分以内に `lessons` として教訓を返します。

`code.callees` と `code.outline` は実験的な機能です（[ステータス](https://github.com/ClickHouse/loom/blob/main/docs/reference/status.md)）。このページのそれ以外は安定版です。
