import { renderMarkdown, type MarkdownRendererOptions } from "@a2ui/markdown-it";

/**
 * Markdown for text the agent writes, with nothing in it that reaches outside the Canvas.
 *
 * `@a2ui/markdown-it` already strips scripts and raw HTML, but keeps ordinary links
 * and images. The agent's text comes from a model, which can be steered by what is
 * in the files it reads, so a link could take the window to another site and an
 * image could carry data out in its address. Links are shown as their text and
 * address; images as their description.
 */
export async function renderAgentMarkdown(
  value: string,
  options?: MarkdownRendererOptions,
): Promise<string> {
  const html = await renderMarkdown(value, options);
  // A template's content is inert: nothing in it loads or runs while it is edited.
  const template = document.createElement("template");
  template.innerHTML = html;
  for (const image of template.content.querySelectorAll("img")) {
    const alt = image.getAttribute("alt");
    image.replaceWith(alt ? `[image: ${alt}]` : "[image]");
  }
  for (const link of template.content.querySelectorAll("a")) {
    const text = link.textContent ?? "";
    const href = link.getAttribute("href") ?? "";
    const span = document.createElement("span");
    span.className = "agent-link";
    span.textContent = href && href !== text ? `${text} (${href})` : text;
    link.replaceWith(span);
  }
  return template.innerHTML;
}
