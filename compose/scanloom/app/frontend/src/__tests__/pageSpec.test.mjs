import assert from "node:assert";
import { describePages, parsePages } from "../lib/pageSpec.ts";
const ok = (spec, total, pages) => assert.deepEqual(parsePages(spec, total).pages, pages, spec);
ok("1-3,6,7-9", 10, [1, 2, 3, 6, 7, 8, 9]);
ok(" 1 - 2 , 4 ,", 4, [1, 2, 4]);
ok("5,1", 5, [5, 1]);
ok("9-7", 9, [9, 8, 7]);
ok("1-3,2,3-4", 4, [1, 2, 3, 4]);
for (const [spec, message] of [["", /which pages/], [" , ", /which pages/], ["0", /no page 0/], ["11", /no page 11/], ["3-11", /no page 11/], ["a", /isn't a page/], ["1-", /isn't a page/], ["-3", /isn't a page/], ["1--2", /isn't a page/], ["1-2-3", /isn't a page/], ["1.5", /isn't a page/], ["1;2", /isn't a page/], ["999999999", /isn't a page/]]) {
  assert.match(parsePages(spec, 10).error ?? "", message, spec);
}
assert.equal(describePages([1, 2, 3, 6, 7, 8, 9]), "1\u20133, 6\u20139");
assert.equal(describePages([5, 1]), "5, 1");
console.log("pageSpec: all passed");
