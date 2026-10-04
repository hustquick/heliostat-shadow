// Multiply blending of sRGB display channels; independent of draw order.
export function multiplyBlend(first, second) {
  if (![first, second].every(value => /^#[0-9a-f]{6}$/i.test(value)))
    throw new Error("Multiply blending requires two six-digit hex colours");
  return "#" + [1, 3, 5].map(offset => {
    const a = parseInt(first.slice(offset, offset + 2), 16);
    const b = parseInt(second.slice(offset, offset + 2), 16);
    return Math.round(a * b / 255).toString(16).padStart(2, "0");
  }).join("");
}
const shadow = "#5be3ff", blocking = "#ff8acc";
export const fieldColors = {
  target: "#ffcc47", body: "#69b6aa", candidate: "#6c8d9b",
  shadow, blocking, overlap: multiplyBlend(shadow, blocking),
  visible: "#64cab0", incoming: "#ff3dbb", reflected: "#f68b4b",
};
