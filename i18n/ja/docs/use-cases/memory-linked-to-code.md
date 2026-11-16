<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=bc08f5ccede7493df24bf88e34857dcfc36ff446 model=claude-sonnet-5-5 -->
> このページは[英語の原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)の機械翻訳です。内容は英語の原文が優先されます。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# コーディングエージェント向けのコードに紐づくメモリ

Loom は、コーディングエージェントのメモリを、そのメモリが名指しする関数、クラス、ファイルに紐づけ、リファクタリングをまたいで追跡し、コードが変わったときにメモリへフラグを付けます。デフォルトでは、コードがすべて消えたメモリは、人が pin、lock、verify していない限り検索結果から除外されます。また、教訓を返すすべての読み取りは、そのコードがいつ変わったか、あるいは消えたことを伝えます。この機能は、セルフホストの Loom がインデックスしたリポジトリ（[コードインデックス](code-intelligence.md)、`LOOM_CODE_INGEST_ENABLED=true`）に対して、MCP、HTTP、SDK 経由で動作し、リンクを維持する `memory_code_links` ワーカーはデフォルトで有効です。

<a id="what-you-get"></a>

## 得られるもの

- **教訓が、対象のコードに紐づく。** `repo:<id>` タグが付いたメモリ（Claude Code と Codex のプラグインは、フックで取得した内容にこのタグを付けます）、またはリポジトリのコード名前空間に書き込まれたメモリは、次の対象にリンクされます。バッククォートで名指しされた定義（関数、クラスなどのシンボル）、あるいは本文中のどこかに `A::b` や呼び出し（`a.b()`、`name()`）の形で現れ、ちょうど 1 か所に解決される定義。メモリが列挙するファイル内にあり、camelCase または snake_case の単語だけで名指しされた定義。そして、列挙されたファイルのうち、これらの定義を 1 つも含まないファイル。まだトランクにない修飾名は、`LOOM_MEMORY_CODE_LINKS_PENDING_DAYS`（14 日）以内にマージされればリンクされます。
  [コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **リファクタリングに追従するリンク。** インデックスされたコミットごとに、各リンクは `fresh`、`moved`、`changed`（変更したコミットを示す）、`unresolved`、`missing` のいずれかになります。リンクは定義のトークンを記録するため、空白、コメント、ほとんどのフォーマッタによる変更では `fresh` のままです。[リンクの状態](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **古くなったメモリを、選んだ方法で扱う。** 名前空間の [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) が方針を決めます。`retire_removed`（デフォルト）は、コードがすべて消えたメモリを検索から除外し、変更されたコードにフラグを付けます。`flag` はフラグを付けるだけです。`revise` はコードが消えたメモリも除外し、さらに変更されたメモリについては、モデルが主張を旧コードと新コードに照らして確認し、修正案を提案します。人が pin、lock、verify したメモリが除外されることはありません。どの方針でも、コードが変わったメモリは、非表示にされる代わりに、検索で次の 3 件より下に順位付けされるのが通常です。保持されたメモリと、名前で見つかった行は順位が保たれます。Loom がメモリ自体を書き換えることはありません。
  [ポリシー](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **エージェントがコードを読む場面ならどこでも教訓が届く。** `code.symbol` は、定義に紐づくメモリを `lessons` として返します。`code.search`、`code.callers`、`code.callees`、`code.impact`、`code.outline`、およびタスクの `code.context_pack` は、返すファイルに対応する教訓を添付します。各教訓にはリンクの状態が付きます。Claude Code と Codex のフックは、エージェントがファイルを編集する前にそのファイルのメモリ（`memory.for_file`）を表示し、Cline のフックはファイルの読み取り時または編集時に表示します。[`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Claude Code プラグイン](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md) ·
  [Codex プラグイン](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/codex.md) ·
  [Cline フック](https://github.com/ClickHouse/loom/blob/main/integrations/cline/README.md)（実験的）
- **チームで共有される。** 教訓は、リポジトリのコード名前空間と、呼び出しで指定された名前空間から読み取られます（MCP 経由では、デフォルトで呼び出し元自身の名前空間）。そのため、リポジトリのコード名前空間に保存された教訓は、その名前空間を読めるすべてのエージェントに届きます（自身のユーザーまたはセッションに限定されたトークンでは、教訓は読み取れません）。
  [コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **ブランチは自身のコードを読む。** 別のブランチでは、教訓はそのブランチ自身の定義に対して確認されるため、古いテキストを保持しているリリースブランチでは `fresh` と判定されます。
  [コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **コードの隣にあるドキュメント。** リポジトリのコード名前空間（そのトランクのもの）に登録されたソースのドキュメントには、リポジトリのタグが付くため、リポジトリに対する検索でコードと一緒に読み取られます（同期ワーカーには `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED` が必要です）。
  [ドキュメントソース](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **レビュー用リスト。**
  [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) は、コードが変更された、再び見つからなかった、または消えたメモリを一覧にします。ダッシュボードには、各メモリのリンクとその状態が表示されます。

![ダッシュボードでのメモリの詳細：リンクされたコード。1 つの定義は最新で、もう 1 つはあるコミット以降に変更されている](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## はじめに

[クイックスタート](../getting-started/quickstart.md)で Loom を起動し、[リポジトリの追加](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)でリポジトリのコードインデックスを有効にします。コーディングエージェントを、プラグインまたは MCP で接続します（[コーディングエージェントを接続する](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）。4 文字以上の定義名をバッククォートで名指しした教訓、または `` `Reader.run` `` のような修飾名を含む教訓を保存します。Claude Code と Codex のプラグインは、フックで取得した内容に `repo:<id>` タグを付け、セッションのプライマーが ID を示します（[導出方法](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)）。エージェント自身が `loom_memory_set` で保存した教訓は、このタグ（`tags: ["repo:<id>"]`）が付いているか、リポジトリのコード名前空間に書き込まれていればリンクされます。どのクライアントでも、どちらも可能です。HTTP（または Python SDK の `agent.call`）では、自分の名前空間に保存した教訓は、呼び出しの `lesson_namespaces` でその名前空間を指定した場合にのみ読み取られます。各リンクパスは作成から 60 秒以上経過した書き込みをリンクし、パスは 60 秒ごとに実行されるため、トランクがインデックスされれば、その定義に対する `code.symbol` は、約 2 分以内に `lessons` の下へその教訓を返します。

このページの一部の op は、[ステータスページ](https://github.com/ClickHouse/loom/blob/main/docs/reference/status.md)で実験的とされています。
