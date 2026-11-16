<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=b1cc9eef83359902a76126407096411d372e2f5e model=claude-sonnet-5-5 -->
> このページは[英語の原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)の機械翻訳です。内容は英語の原文が優先されます。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# コーディングエージェント向けのコードに紐づくメモリ

Loom は、コーディングエージェントのメモリを、そのメモリが名指しする関数、クラス、ファイルに紐づけ、リファクタリングをまたいで追跡し、対応するコードが変更されたときにそのメモリにフラグを立てます。デフォルトでは、コードがすべて失われたメモリは、人が固定、ロック、または検証していない限り検索結果から除外され、教訓を返すすべての読み取りは、そのコードがいつ変更されたか、または失われたかを示します。これは MCP、HTTP、SDK を通じて動作し、セルフホストの Loom がインデックスしたリポジトリ（[コードインデックス](code-intelligence.md)、`LOOM_CODE_INGEST_ENABLED=true`）を対象とします。リンクを維持する `memory_code_links` ワーカーはデフォルトで有効です。

<a id="what-you-get"></a>

## 得られるもの

- **教訓を、その対象のコードに紐づけ。** `repo:<id>` タグが付いたメモリ（Claude Code と Codex のプラグインは、フックが取り込んだものにこのタグを付けます）、またはリポジトリのコード名前空間に書かれたメモリは、次のものにリンクされます。バッククォートで名指しされた定義（関数、クラス、その他のシンボル）、または本文中のどこかに `A::b` や呼び出し（`a.b()`、`name()`）の形で現れ、ちょうど 1 か所に解決される定義。メモリが列挙するファイル内の定義のうち、camelCase または snake_case の単語だけで名指しされたもの。そして、メモリが列挙するファイルのうち、これらの定義をどれも含まないもの。まだ trunk にない修飾名は、`LOOM_MEMORY_CODE_LINKS_PENDING_DAYS`（14 日）以内にマージされればリンクされます。
  [コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **リファクタリングに追従するリンク。** インデックスされるコミットごとに、各リンクは `fresh`、`moved`、`changed`（変更したコミットを示す）、`unresolved`、`missing` のいずれかになります。リンクは定義のトークンを記録するため、空白、コメント、ほとんどのフォーマッタによる変更では `fresh` のままです。[リンクの状態](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **古くなったメモリを、選んだ方法で処理。** 名前空間の [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) が決めます。`retire_removed`（デフォルト）は、コードがすべて失われたメモリを検索から除外し、変更されたコードにフラグを立てます。`flag` はフラグを立てるだけです。`revise` はコードが失われたメモリも除外し、さらに変更されたメモリについて、モデルに旧コードと新コードを照らして主張を確認させ、修正した主張を提案させます。人が固定、ロック、または検証したメモリが除外されることはありません。どのポリシーでも、コードが変更されたメモリは、隠されるのではなく、検索で通常は次の 3 件の結果より下に順位づけされます。保持されたメモリと名前で見つかった行は順位を保ちます。Loom がメモリ自体を書き換えることはありません。
  [ポリシー](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **エージェントがコードを読む場所すべてで教訓を提示。** `code.symbol` は定義に紐づくメモリを `lessons` として返します。`code.search`、`code.callers`、`code.callees`、`code.impact`、`code.outline`、およびタスクの `code.context_pack` は、返すファイルの教訓を付加します。各教訓にはリンクの状態が付きます。Claude Code と Codex のフックは、エージェントがファイルを編集する前にそのファイルのメモリ（`memory.for_file`）を表示し、Cline のフックはファイルを読むときや編集するときに表示します。[`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ・
  [Claude Code プラグイン](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md) ・
  [Codex プラグイン](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/codex.md)
- **チームで共有。** 教訓は、リポジトリのコード名前空間と、呼び出しで指定された名前空間（MCP ではデフォルトで呼び出し元自身のもの）から読み取られます。そのため、リポジトリのコード名前空間に保存された教訓は、その名前空間を読めるすべてのエージェントに届きます（自分のユーザーまたはセッションに限定されたトークンでは教訓は読み取れません）。
  [コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **ブランチは自身のコードを読む。** 別のブランチでは、教訓はそのブランチ自身の定義と照合されるため、古いテキストをまだ持つリリースブランチでは `fresh` と読み取られます。
  [コードに紐づくメモリ](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **コードと並ぶドキュメント。** リポジトリのコード名前空間（trunk のもの）に登録されたソースのドキュメントにはリポジトリのタグが付くため、リポジトリを対象とする検索ではコードと一緒に読み取られます（同期ワーカーには `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED` が必要です）。
  [ドキュメントソース](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **レビュー用リスト。** [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) は、コードが変更された、再び見つからなかった、または失われたメモリを一覧し、ダッシュボードは各メモリのリンクとその状態を表示します。

![ダッシュボードのメモリ詳細：リンクされたコード。1 つの定義は最新で、もう 1 つはあるコミット以降に変更されている](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## はじめに

[クイックスタート](../getting-started/quickstart.md)で Loom を起動し、[リポジトリの追加](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)でリポジトリのコードインデックスを有効にします。コーディングエージェントを、そのプラグインまたは MCP で接続します（[コーディングエージェントを接続する](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）。4 文字以上の定義、または `` `Reader.run` `` のような修飾名をバッククォートで名指しした教訓を保存します。Claude Code と Codex のプラグインは、フックが取り込んだものに `repo:<id>` を付け、セッションの primer が id を示します（[導出方法](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)）。エージェントが `loom_memory_set` で自分で保存した教訓は、このタグ（`tags: ["repo:<id>"]`）を持つか、リポジトリのコード名前空間に書かれていればリンクされます。どのクライアントでもどちらも可能です。HTTP（または Python SDK の `agent.call`）では、自分の名前空間に保存された教訓は、呼び出しが `lesson_namespaces` でその名前空間を指定した場合にのみ読み取られます。各リンク処理は、書き込みから 60 秒以上経ったものをリンクし、処理は 60 秒ごとに実行されるため、trunk がインデックスされれば、その定義に対する `code.symbol` は約 2 分以内に `lessons` の下でその教訓を返します。

`code.callees` と `code.outline` は実験的です（[ステータス](https://github.com/ClickHouse/loom/blob/main/docs/reference/status.md)）。このページの残りは安定版です。
