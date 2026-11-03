"""One real component supplies paging, sorting, filtering and safe text cells."""
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
WEB=Path(__file__).parents[1]/'src/delivery_api/web'

def test_table_owns_filter_sort_paging_and_text_cells():
    with sync_playwright() as p:
        browser=p.chromium.launch(args=['--no-sandbox']);page=browser.new_page()
        page.route('http://table.test/**',lambda route:route.fulfill(content_type='text/javascript',body=(WEB/route.request.url.split('/')[-1]).read_text()) if route.request.url.endswith('.js') else route.fulfill(content_type='text/html',body='<main></main>'))
        page.goto('http://table.test/')
        page.evaluate("""async()=>{const {Table}=await import('/table-component.js');window.Table=Table;
        const table=Table.interactive(document.querySelector('main'),'Rows','tip',[{key:'n',label:'Name'},{key:'a',label:'Amount',num:true}],'Nothing yet.');
        table.set(Array.from({length:12},(_,i)=>({n:i===0?'<img src=x onerror=alert(1)>':'Row '+i,a:12-i})));} """)
        expect(page.locator('tbody tr')).to_have_count(10)
        expect(page.locator('tbody img')).to_have_count(0)
        page.get_by_role('button',name='Next',exact=True).click()
        expect(page.locator('tbody tr')).to_have_count(2)
        page.locator('th').filter(has_text='Amount').click()
        page.get_by_role('button',name='Previous',exact=True).click()
        expect(page.locator('tbody tr').first.locator('td').last).to_have_text('1')
        page.get_by_placeholder('Filter').fill('not present')
        expect(page.locator('thead th')).to_have_count(2)
        expect(page.locator('tbody')).to_have_text('No rows match the filter.')
        page.get_by_placeholder('Filter').fill('Row')
        expect(page.locator('tbody tr')).to_have_count(10)
        page.locator('select').select_option('25')
        expect(page.locator('tbody tr')).to_have_count(11)
        assert page.evaluate("document.querySelectorAll('#ui-table-styles').length")==1
        assert page.evaluate("Table.rows([{x:'b'},{x:'A'}],{sort:'x',compare:(a,b)=>a>b?1:a<b?-1:0}).map(r=>r.x)")==['A','b']
        browser.close()
