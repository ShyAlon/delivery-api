from test_debug_progress_browser import ui, page, _open_debug


def test_feedback_read_failure_is_visible_and_retryable(page, ui):
    page.route('**/api/tasks/'+page._debug_task_id+'/feedback',lambda r:r.fulfill(status=503,content_type='application/json',body='{"detail":"offline"}'))
    page.goto(ui+'/tasks/'+page._debug_task_id+'/view')
    page.locator('#feedbackunavailable').wait_for(timeout=3000)
    assert 'Feedback unavailable' in page.locator('#feedbackunavailable').inner_text()
    page.screenshot(path='/downloads/row12-05-feedback.png',full_page=True)
    page.unroute('**/api/tasks/'+page._debug_task_id+'/feedback')
    page.route('**/api/tasks/'+page._debug_task_id+'/feedback',lambda r:r.fulfill(content_type='application/json',body='{"feedback":[]}'))
    page.locator('#retryfeedback').click()
    page.wait_for_function("() => !document.querySelector('#feedbackunavailable')")


def test_failed_feedback_keeps_prior_notes_and_disables_edits(page,ui):
    page.goto(ui+'/tasks/'+page._debug_task_id+'/view')
    page.locator('#accountbutton').wait_for()
    result=page.evaluate('''async () => {
      const {createQa}=await import('/assets/qa.js');
      const qa=createQa({});
      qa.setFeedbackState([{target:'question',input_ref:'ref',thumb:1,comment:'Known note'}],true);
      const before=qa.qaNotesHtml('question','ref');
      qa.setFeedbackState([{target:'question',input_ref:'ref',thumb:1,comment:'Known note'}],false);
      const el=document.createElement('div');el.innerHTML=qa.qaActionsHtml('question','ref');
      qa.wireQaActions(el.firstElementChild);
      return {before,after:qa.qaNotesHtml('question','ref'),disabled:[...el.querySelectorAll('button')].every(b=>b.disabled)};
    }''')
    assert result['before']==result['after'] and 'Known note' in result['after'] and result['disabled']
