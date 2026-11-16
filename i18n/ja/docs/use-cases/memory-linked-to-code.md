<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=398a66da6f0537777da91f8146bf14723fcb2c87 model=claude-sonnet-5-5 -->
> このページは[英語の原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)の機械翻訳です。内容は英語の原文が優先されます。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# コーディングエージェント向けのコードに紐づくメモリ

Loom は、コーディングエージェントのメモリを、そのメモリが名指しする関数、クラス、ファイルに紐づけ、リファクタリングをまたいで追跡し、対象のコードが変更されるとメモリにフラグを立てます。デフォルトでは、コードがすべて失われたメモリは、人がピン留め、ロック、または検証していない限り検索結果から除外されます。また、教訓（lesson）を返すすべての読み取りは、そのコードがいつ変更されたか、または失われたことを伝えます。この機能は、セルフホストの Loom がインデックスしたリポジトリ（[コードインデックス](code-intelligence.md)、`LOOM_CODE_INGEST_ENABLED=true`）に対して MCP、HTTP、SDK 経由で動作し、リンクを維持する `memory_code_links` ワーカーはデフォルトで有効です。

<a id="what-you-get"></a>

## 得られるもの

- **対象のコードに紐づいた教訓。** `repo:<id>` タグが付いたメモリ（Claude Code と Codex のプラグインは、フックが取得した内容にこのタグを付けます）、またはリポジトリのコード名前空間に書き込まれたメモリは、次のものにリンクされます。バッククォートで名指しされた定義（関数、クラス、その他のシンボル）、または本文中の `A::b` や呼び出し（`a.b()`、`name()`）の形で名指しされ、ちょうど 1 か所に解決される定義（修飾なしの名前は 4 文字以上が必要）。メモリが列挙するファイル内の定義のうち、camelCase または snake_case の単語そのままで名指しされたもの。そして、メモリが列挙するファイルのうち、上記の定義をひとつも含まないもの。まだトランクにない修飾名は、`LOOM_MEMORY_CODE_LINKS_PENDING_DAYS`（14 日）以内にマージされればリンクされます。
  [コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **リファクタリングに追従するリンク。** インデックスされたコミットごとに、各リンクは `fresh`、`moved`、`changed`（変更したコミットを示す）、`unresolved`、`missing` のいずれかになります。リンクは定義のトークンを記録するため、空白、コメント、ほとんどのフォーマッタによる変更では `fresh` のままです。[リンクの状態](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **古くなったメモリを選んだ方法で扱う。** 名前空間の [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) が決めます。`retire_removed`（デフォルト）は、コードがすべて失われたメモリを検索から除外し、変更されたコードにフラグを立てます。`flag` はフラグを立てるだけです。`revise` はコードが失われたメモリを同様に除外し、さらに変更されたメモリの主張を、モデルが古いコードと新しいコードに照らして確認します。主張が今も成り立つメモリは再アンカーされ、矛盾するメモリには修正案が提示されます。人がピン留め、ロック、または検証したメモリが除外されることはありません。どのポリシーでも、コードが変更されたメモリは、通常、隠されるのではなく、検索で次の 3 件の結果より下に順位付けされます。保持されているメモリと、名前で見つかった行は順位を保ちます。Loom がメモリ自体を書き換えることはありません。
  [ポリシー](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **エージェントがコードを読むあらゆる場面での教訓。** `code.symbol` は定義に紐づくメモリを `lessons` として返します。`code.search`、`code.callers`、`code.callees`、`code.impact`、`code.outline`、およびタスクの `code.context_pack` は、返すファイルに対する教訓を付加します。各教訓はリンクの状態を持ちます。Claude Code と Codex のフックは、エージェントがファイルを編集する前にそのファイルのメモリ（`memory.for_file`）を表示し、Cline のフックはファイルの読み取りまたは編集時に表示します。[`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Claude Code プラグイン](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md) ·
  [Codex プラグイン](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/codex.md) ·
  [Cline フック](https://github.com/ClickHouse/loom/blob/main/integrations/cline/README.md)（実験的）
- **チームでの共有。** 教訓は、リポジトリのコード名前空間と、呼び出しが指定する名前空間から読み取られます（MCP では、デフォルトで呼び出し元自身の名前空間）。そのため、リポジトリのコード名前空間に保存された教訓は、その名前空間を読み取れるすべてのエージェントに届きます（自分のユーザーまたはセッションに限定されたトークンでは、教訓は読み取れません）。
  [コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **ブランチごとに自分のコードを読む。** 別のブランチでは、教訓はそのブランチ自身の定義に照らして確認されるため、古いテキストを持つリリースブランチでは `fresh` と表示されます。
  [コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **コードの隣にあるドキュメント。** リポジトリのコード名前空間（そのトランクのもの）に登録されたソースからのドキュメントには、リポジトリのタグが付くため、リポジトリに対する検索でコードと一緒に読まれます（同期ワーカーには `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED` が必要です）。
  [ドキュメントソース](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **レビュー用リスト。** [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) は、コードが変更された、再び見つからなかった、または失われたメモリを一覧表示します。ダッシュボードには各メモリのリンクとその状態が表示されます。

![ダッシュボードでのメモリの詳細：リンクされたコード。1 つの定義は最新で、もう 1 つはあるコミット以降に変更されている](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## 開始

[クイックスタート](../getting-started/quickstart.md)で Loom を起動し、[リポジトリの追加](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)でリポジトリに対してコードインデックスを有効にします。プラグインまたは MCP でコーディングエージェントを接続します（[コーディングエージェントの接続](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）。4 文字以上の定義をバッククォートまたは呼び出し（`` `name()` ``）で名指しする教訓、または `` `Reader.run` `` のような修飾名を含む教訓を保存します。Claude Code と Codex のプラグインは、フックが取得した内容に `repo:<id>` のタグを付け、セッションのプライマーが id を示します（[導出方法](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)）。エージェントが `loom_memory_set` で自分で保存した教訓は、そのタグ（`tags: ["repo:<id>"]`）を持つか、リポジトリのコード名前空間に書き込まれている場合にリンクされます。どちらもどのクライアントからでも可能です。HTTP（または Python SDK の `agent.call`）では、自分の名前空間に保存された教訓は、呼び出しが `lesson_namespaces` でその名前空間を指定した場合にのみ読み取られます。各リンクパスは作成から 60 秒以上経過した書き込みをリンクし、パスは 60 秒ごとに実行されるため、トランクがインデックスされると、その定義に対する `code.symbol` は約 2 分以内に `lessons` の下に教訓を返します。

このページの一部の op は、[ステータスページ](https://github.com/ClickHouse/loom/blob/main/docs/reference/status.md)で実験的とされています。
