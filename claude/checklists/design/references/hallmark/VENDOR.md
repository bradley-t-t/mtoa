# Vendored: Hallmark

Upstream: https://github.com/Nutlope/hallmark (the `skills/hallmark/` subtree)
License: MIT, in LICENSE alongside this file. Copyright the Hallmark contributors, Together AI.
Version: 1.1.0

This directory is a verbatim copy of Hallmark's skill content, folded into the design
skill as the `hallmark` domain (see the design SKILL.md domain map). It is third-party
material: do not hand-edit it, and hold it apart from the comment, documentation, and
writing passes. Re-sync from upstream to update:

  git clone --depth 1 https://github.com/Nutlope/hallmark /tmp/hallmark
  find . -mindepth 1 -not -name VENDOR.md -delete
  cp -R /tmp/hallmark/skills/hallmark/. .
  cp /tmp/hallmark/LICENSE ./LICENSE
  mkdir -p docs && cp /tmp/hallmark/docs/recipes.md /tmp/hallmark/docs/study-examples.md docs/
