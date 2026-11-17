type Viewport = {
  convertToPdfPoint: (x: number, y: number) => number[];
  convertToViewportPoint: (x: number, y: number) => number[];
}
// Backend coordinates use the intrinsic, rotated physical page's top-left.
export function highlightBox(bbox: number[], base: Viewport, target: Viewport) {
  const points = [[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]], [bbox[0], bbox[3]]]
    .map(([x, y]) => base.convertToPdfPoint(x, y))
    .map(([x, y]) => target.convertToViewportPoint(x, y))
  const xs = points.map(p => p[0]), ys = points.map(p => p[1])
  return { left: Math.min(...xs), top: Math.min(...ys), width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys) }
}
