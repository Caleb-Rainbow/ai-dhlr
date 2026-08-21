/**
 * TransitionGroup 删除动画辅助。
 *
 * .list-leave-active 会让离开的元素 position: absolute（以便 .list-move 平滑补位），
 * 但绝对定位的 grid 子元素会脱离列轨道：宽度变成容器的 100% 或内容宽度，
 * 静态位置也会跳到容器左上角。此钩子在元素脱离文档流前（before-leave 时
 * 仍处于正常流中）把它当前的几何位置钉成内联样式，使其原地淡出。
 *
 * 用法: <TransitionGroup @before-leave="pinLeaveGeometry" ...>
 */
export function pinLeaveGeometry(el: Element) {
    const htmlEl = el as HTMLElement;
    const parent = htmlEl.offsetParent as HTMLElement | null;
    if (!parent) return;

    const rect = htmlEl.getBoundingClientRect();
    const parentRect = parent.getBoundingClientRect();

    // 卡片自身带 transition-all，会拖拽钉位过程，直接关掉（离场动画走 CSS animation 不受影响）
    htmlEl.style.transition = 'none';
    htmlEl.style.width = `${rect.width}px`;
    htmlEl.style.height = `${rect.height}px`;
    htmlEl.style.top = `${rect.top - parentRect.top}px`;
    htmlEl.style.left = `${rect.left - parentRect.left}px`;
    htmlEl.style.margin = '0';
    htmlEl.style.pointerEvents = 'none';
}
