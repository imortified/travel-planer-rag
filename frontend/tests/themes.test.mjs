import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { themes, themeBootstrap, themeStorageKey, validTheme } from "../lib/themes.ts";

test("only the three explicit theme choices can be restored",()=>{
  for(const theme of themes)assert.equal(validTheme(theme.id),theme.id);
  for(const value of [null,undefined,"system","<script>",{},true])assert.equal(validTheme(value),"warm");
});

test("the pre-paint script restores each saved theme and handles blocked or invalid storage",()=>{
  for(const saved of ["warm","cool","dark",null,"unknown","blocked"]){
    const document={documentElement:{dataset:{}}};
    const localStorage={getItem(key){assert.equal(key,themeStorageKey);if(saved==="blocked")throw new Error("Denied");return saved;}};
    runInNewContext(themeBootstrap,{document,localStorage});
    assert.equal(document.documentElement.dataset.theme,validTheme(saved));
  }
});

function luminance(hex) {
  const channels=hex.slice(1).match(/../g).map(value=>parseInt(value,16)/255)
    .map(value=>value<=.04045?value/12.92:((value+.055)/1.055)**2.4);
  return channels[0]*.2126+channels[1]*.7152+channels[2]*.0722;
}
function contrast(a,b) {const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}

test("body text, form labels and primary buttons remain legible in the new palettes",async()=>{
  const css=await readFile(new URL("../app/themes.css",import.meta.url),"utf8");
  for(const theme of ["cool","dark"]){
    const block=css.match(new RegExp(`:root\\[data-theme="${theme}"\\] \\{([^}]+)\\}`))[1];
    const colors=Object.fromEntries([...block.matchAll(/--([\w-]+):\s*(#[0-9a-f]+);/g)].map(match=>[match[1],match[2]]));
    for(const [foreground,background] of [["ink","paper"],["ink","surface"],["muted","surface"],["label","surface"],["on-primary","green"]]){
      assert.ok(contrast(colors[foreground],colors[background])>=4.5,`${theme}: ${foreground} on ${background}`);
    }
  }
});
