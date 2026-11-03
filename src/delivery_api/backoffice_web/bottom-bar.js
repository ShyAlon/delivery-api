export async function mountBottomBar(host,read){
 const footer=document.createElement('footer');footer.className='operator-bottom-bar';const note=document.createElement('span');note.textContent='Private Back office · Checking running build commits...';const link=document.createElement('a');link.href='#debug';link.textContent='Debug evidence';footer.append(note,link);host.append(footer);
 try{const {repositories}=await read('build');note.textContent='Private Back office · '+['delivery-api','interview-engine'].map(name=>name+' '+(repositories?.[name]||'unreported')).join(' · ');}catch{note.textContent='Private Back office · Build check failed. Refresh or open Debug.';}
 return footer;
}

