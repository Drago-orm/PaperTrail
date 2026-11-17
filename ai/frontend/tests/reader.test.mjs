import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { getDocument } from 'pdfjs-dist/legacy/build/pdf.mjs'
import { highlightBox } from '../src/coordinates.ts'

test('physical page rectangles remain aligned across sizes, scale and rotations', async () => {
  const bytes = await readFile(new URL('../../fixtures/reader.pdf', import.meta.url))
  const loading = getDocument({ data: new Uint8Array(bytes) })
  const pdf = await loading.promise
  try {
    for (const [pageNumber, width, height] of [[1, 600, 800], [2, 420, 600]]) {
      const page = await pdf.getPage(pageNumber)
      const base = page.getViewport({ scale: 1 })
      assert.equal(base.width, width)
      assert.equal(base.height, height)
      for (const scale of [.5, 1, 1.5, 2]) {
        // Golden rectangles for a top-left physical-page box (40,40)-(160,60).
        const expected = [[40,40,120,20], [height-60,40,20,120],
          [width-160,height-60,120,20], [40,width-160,20,120]]
        for (const [index, rotation] of [0,90,180,270].entries()) {
          const actual = highlightBox([40,40,160,60], base, page.getViewport({ scale, rotation }))
          for (const [i, key] of ['left','top','width','height'].entries()) {
            assert.ok(Math.abs(actual[key]-expected[index][i]*scale)<.001, `${pageNumber}/${scale}/${rotation}/${key}`)
          }
        }
      }
    }
  } finally { await loading.destroy() }
})
