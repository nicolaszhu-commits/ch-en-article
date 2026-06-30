# articles/

每篇文章一个子目录：

```
articles/<slug>/
  source.md       原始中文文章
  working-doc.md   工作底稿（由模板生成，逐阶段填充）
  output-en.md     最终英文文章
```

新建一篇：把中文原文存入 `articles/<slug>/source.md`，然后告诉 Kiro「按流水线处理 <slug>」。
