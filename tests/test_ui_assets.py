import shutil
import subprocess

import pytest
from jinja2 import Environment, FileSystemLoader

from takeaway_insight.config import PACKAGE_DIR


def test_app_navigation_and_accessible_work_area_are_rendered_without_remote_assets():
    environment = Environment(loader=FileSystemLoader(PACKAGE_DIR / "templates"), autoescape=True)
    html = environment.get_template("app.html").render(
        page="orders", csrf_token="test", username="admin"
    )
    for path in ("/orders", "/sales", "/rules", "/merchants", "/syncs", "/mcp-guide", "/agents"):
        assert f'href="{path}"' in html
    assert 'id="main-content"' in html
    assert 'id="page-content"' in html
    assert 'name="csrf-token" content="test"' in html
    assert 'aria-live="polite"' in html
    assert "https://fonts" not in html


def test_login_keeps_native_form_and_labeled_credentials():
    environment = Environment(loader=FileSystemLoader(PACKAGE_DIR / "templates"), autoescape=True)
    html = environment.get_template("login.html").render(csrf_token="token", error="错误")
    assert 'name="csrf_token" value="token"' in html
    assert 'autocomplete="current-password"' in html
    assert 'role="alert"' in html
    assert '<label for="password">' in html


def test_ui_assets_exist_and_do_not_call_remote_cdn():
    script = (PACKAGE_DIR / "static" / "app.js").read_text()
    assert "X-CSRF-Token" in script
    assert "textContent" in script
    assert "/api/rules/preview" in script
    assert "quantity_by_unit" in script
    assert "原始下单口径" in script
    assert "cdn.jsdelivr" not in script
    assert (PACKAGE_DIR / "static" / "app.css").is_file()


@pytest.mark.skipif(shutil.which("node") is None, reason="此 JavaScript 行为回归需要开发环境 Node")
def test_filter_changes_update_navigation_and_top_order_link_without_changing_exports():
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('  function saveFilters() {');
const end = source.indexOf('  function ensureDates()', start);
assert.ok(start >= 0 && end > start);
const origin = 'http://127.0.0.1:8765';
function link(path, navigation, preserve) {
  let current = new URL(path, origin).href;
  return {navigation, preserve, get href() { return current; },
    set href(value) { current = new URL(value, origin).href; }};
}
const sidebar = link('/orders', true, false);
const topOrder = link('/orders?merchant_id=1&start_date=2026-11-17&end_date=2026-11-17', false, true);
const exportLink = link('/api/export/sales.csv?start_date=2026-11-17&end_date=2026-11-17', false, false);
const all = [sidebar, topOrder, exportLink];
let query = new URLSearchParams('merchant_id=2&platform=jd&start_date=2026-11-10&end_date=2026-11-16');
const history = [];
const context = {
 URL, filters: () => query, location: {pathname: '/'},
 history: {replaceState: (...args) => history.push(args)},
 $$: selector => all.filter(item =>
   (selector.includes('.nav-link') && item.navigation) ||
   (selector.includes('[data-preserve-filters]') && item.preserve)),
};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
context.saveFilters();
for (const target of [sidebar, topOrder]) {
 const result = new URL(target.href);
 assert.equal(result.pathname, '/orders');
 assert.equal(result.searchParams.get('merchant_id'), '2');
 assert.equal(result.searchParams.get('start_date'), '2026-11-10');
 assert.equal(result.searchParams.get('end_date'), '2026-11-16');
 assert.equal(result.searchParams.get('platform'), 'jd');
}
assert.equal(new URL(exportLink.href).searchParams.get('start_date'), '2026-11-17');
query = new URLSearchParams('start_date=2026-11-17&end_date=2026-11-17');
context.saveFilters();
assert.equal(new URL(topOrder.href).searchParams.has('merchant_id'), false);
assert.equal(new URL(topOrder.href).searchParams.get('start_date'), '2026-11-17');
assert.equal(new URL(exportLink.href).searchParams.get('start_date'), '2026-11-17');
assert.equal(history.length, 2);
"""
    result = subprocess.run(
        ["node", "-e", script, str(PACKAGE_DIR / "static" / "app.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="此 JavaScript 行为回归需要开发环境 Node")
def test_delayed_rule_preview_never_uses_changed_inputs_or_a_replacement_form():
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('  async function previewRule() {');
const end = source.indexOf('  async function merchants(', start);
assert.ok(start >= 0 && end > start);
function form(quantity = '2') {
 const target = {hidden: true, innerHTML: '', textContent: '', replaceChildren() { this.innerHTML = ''; }};
 return {isConnected: true, preview: target, parts: [{name:'鸡',factor:'1',unit:'份'}],
   elements: {match_name:{value:'半只鸡+半只鸭'}, preview_quantity:{value:quantity}}, reportValidity:()=>true};
}
let current, resolve, pendingBody;
const errors = [];
const editor = {open:true,contains: node => node === current};
const context = {
 $: (selector, root) => selector === '#editor' ? editor : selector === '#rule-form' ? current : selector === '#rule-preview' ? (root || current).preview : null,
 ruleParts: value => value.parts.map(part=>({...part})),
 api: (path, options) => {pendingBody=options.body;return new Promise(done=>{resolve=done;});},
 escape: value => String(value), formError: (value,message)=>errors.push([value,message]),
};
vm.createContext(context);
vm.runInContext(source.slice(start,end),context);
const response = {items:[{name:'鸡',quantity:'2',unit:'份'}]};
(async()=>{
 current=form();
 let result=context.previewRule();
 assert.equal(pendingBody.quantity,'2');
 current.elements.preview_quantity.value='3';
 resolve(response);await result;
 assert.ok(!current.preview.innerHTML.includes('购买 3'));
 assert.ok(!current.preview.innerHTML.includes('preview-item'));
 current=form();
 result=context.previewRule();
 current.parts[0].factor='2';
 resolve(response);await result;
 assert.ok(!current.preview.innerHTML.includes('preview-item'));
 current=form();
 const previous=current;
 result=context.previewRule();
 previous.isConnected=false;
 current=form('4');
 current.preview.innerHTML='new form untouched';
 resolve(response);await result;
 assert.equal(current.preview.innerHTML,'new form untouched');
 current=form();
 result=context.previewRule();
 resolve(response);await result;
 assert.ok(current.preview.innerHTML.includes('购买 2 份'));
 assert.ok(current.preview.innerHTML.includes('preview-item'));
 assert.equal(errors.length,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        ["node", "-e", script, str(PACKAGE_DIR / "static" / "app.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
