(() => {
  const root = document.getElementById('xmind-map');
  if (!root) return;
  const sheets = JSON.parse(document.getElementById('xmind-map-data').textContent);
  const tabs = root.querySelector('.xmind-tabs');
  const viewport = root.querySelector('.xmind-canvas');
  const panel = root.querySelector('[role="tabpanel"]');
  const notesTitle = root.querySelector('.xmind-notes-title');
  const notesText = root.querySelector('.xmind-notes-text');
  const counter = root.querySelector('.xmind-count');
  const NS = 'http://www.w3.org/2000/svg';
  const W = 230, GAP = 70, SPACE = 22, PAD = 36;
  const colors = ['#5986a7','#8b819e','#6b968a','#b19364','#7895b4','#7d9d66'];
  let selected = 0, expanded = new Set(), zoom = 1, drawing = null;
  const children = n => n.children?.attached || [];
  const totalNodes = n => 1 + children(n).reduce((sum,c) => sum + totalNodes(c),0);
  const svgEl = (tag, attrs={}, value) => {
    const el = document.createElementNS(NS,tag);
    Object.entries(attrs).forEach(([k,v]) => el.setAttribute(k,String(v)));
    if (value !== undefined) el.textContent = value;
    return el;
  };
  function lines(title) {
    const words = title.split(/\s+/), result=[];
    let line='';
    words.forEach(word => {
      while (word.length > 24) {
        if (line) {result.push(line);line='';}
        result.push(word.slice(0,24));word=word.slice(24);
      }
      if ((line+' '+word).trim().length > 24) {result.push(line);line=word;}
      else line=(line+' '+word).trim();
    });
    if(line)result.push(line);
    if(result.length>3)return result.slice(0,2).concat(result[2].slice(0,22)+'…');
    return result;
  }
  function showNotes(topic) {
    notesTitle.textContent=topic.title;
    notesText.textContent=topic.notes?.plain?.content || (children(topic).length ? children(topic).length+' subtopics. Click the topic to expand or collapse its branch.' : 'No additional notes for this topic.');
  }
  function updateSize(center=false) {
    if(!drawing)return;
    const svg=viewport.querySelector('svg');
    svg.style.width=(drawing.width*zoom)+'px';
    svg.style.height=(drawing.height*zoom)+'px';
    if(center) viewport.scrollLeft=Math.max(0,(drawing.rootX+W/2)*zoom-viewport.clientWidth/2);
  }
  function fit() {
    if(!drawing)return;
    zoom=Math.max(.7,Math.min(1,viewport.clientWidth/drawing.width));
    updateSize(true);
  }
  function render(center=false) {
    const topic=sheets[selected].rootTopic;
    const nodes=[],edges=[];
    function make(n,depth,color) {
      const label=lines(n.title),h=Math.max(50,label.length*19+24);
      const kids=expanded.has(n.id)?children(n).map(c=>make(c,depth+1,color)):[];
      const total=Math.max(h,kids.reduce((a,c)=>a+c.total,0)+Math.max(0,kids.length-1)*SPACE);
      return {topic:n,depth,label,h,kids,total,color};
    }
    const head=make({...topic,children:{}},0,'#173d57');
    const branches=children(topic).map((n,i)=>({...make(n,1,colors[i%colors.length]),side:i%2===0?1:-1}));
    function depth(n){return n.kids.length?Math.max(n.depth,...n.kids.map(depth)):n.depth;}
    const left=branches.filter(n=>n.side<0),right=branches.filter(n=>n.side>0);
    const ld=left.length?Math.max(...left.map(depth)):0,rd=right.length?Math.max(...right.map(depth)):0;
    const stack=arr=>arr.reduce((v,n)=>v+n.total,0)+Math.max(0,arr.length-1)*SPACE;
    const height=Math.max(head.h,stack(left),stack(right))+2*PAD;
    const width=W+(ld+rd)*(W+GAP)+2*PAD;
    const rootX=PAD+ld*(W+GAP);
    head.x=rootX;head.y=height/2-head.h/2;head.topic=topic;nodes.push(head);
    function place(n,side,top,parent) {
      n.x=rootX+side*n.depth*(W+GAP);n.y=top+n.total/2-n.h/2;nodes.push(n);edges.push({from:parent,to:n,color:n.color,side});
      let next=top+(n.total-stack(n.kids))/2;
      n.kids.forEach(c=>{place(c,side,next,n);next+=c.total+SPACE;});
    }
    [left,right].forEach(arr=>{let y=(height-stack(arr))/2;arr.forEach(n=>{place(n,n.side,y,head);y+=n.total+SPACE;});});
    const svg=svgEl('svg',{viewBox:'0 0 '+width+' '+height,role:'group','aria-label':sheets[selected].title+' interactive mind map'});
    svg.append(svgEl('title',{},sheets[selected].title+' — click topics to expand and read notes'));
    edges.forEach(e=>{
      const x1=e.from.x+(e.side>0?W:0), y1=e.from.y+e.from.h/2;
      const x2=e.to.x+(e.side>0?0:W), y2=e.to.y+e.to.h/2,mid=(x1+x2)/2;
      svg.append(svgEl('path',{d:'M'+x1+','+y1+' C'+mid+','+y1+' '+mid+','+y2+' '+x2+','+y2,fill:'none',stroke:e.color,'stroke-width':2}));
    });
    nodes.forEach(n=>{
      const isRoot=n===head,hasKids=children(n.topic).length>0;
      const g=svgEl('g',{class:'xmind-topic',role:'button',tabindex:'0','aria-label':n.topic.title+(hasKids&&!isRoot?'; '+(expanded.has(n.topic.id)?'collapse':'expand')+' branch':''),transform:'translate('+n.x+','+n.y+')'});
      if(hasKids)g.setAttribute('aria-expanded',String(isRoot||expanded.has(n.topic.id)));
      g.append(svgEl('rect',{class:'xmind-node-box',width:W,height:n.h,rx:9,fill:isRoot?'#173d57':'#ffffff',stroke:n.color,'stroke-width':isRoot?2:1.6}));
      const text=svgEl('text',{class:'xmind-node-label',fill:isRoot?'#ffffff':'#183044','text-anchor':'middle',x:W/2-4,y:(n.h-(n.label.length-1)*19)/2+5});
      n.label.forEach((line,i)=>text.append(svgEl('tspan',{x:W/2-4,dy:i?19:0},line)));
      g.append(text);
      if(hasKids&&!isRoot)g.append(svgEl('text',{class:'xmind-toggle',x:W-17,y:n.h/2+5,fill:n.color,'text-anchor':'middle'},expanded.has(n.topic.id)?'−':'+'));
      const activate=()=>{showNotes(n.topic);if(hasKids&&!isRoot){if(expanded.has(n.topic.id))expanded.delete(n.topic.id);else expanded.add(n.topic.id);render(false);const focus=viewport.querySelector('[data-topic-id="'+n.topic.id+'"]');focus?.focus({preventScroll:true});}};
      g.dataset.topicId=n.topic.id;g.addEventListener('click',activate);g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();activate();}});
      svg.append(g);
    });
    viewport.replaceChildren(svg);drawing={width,height,rootX};updateSize(center);
    counter.textContent=nodes.length+' of '+totalNodes(topic)+' topics visible · '+Math.round(zoom*100)+'% zoom';
  }
  sheets.forEach((sheet,i)=>{
    const b=document.createElement('button');b.type='button';b.role='tab';b.id='xmind-sheet-'+i;b.setAttribute('aria-controls','xmind-sheet-panel');b.textContent=sheet.title;
    b.onclick=()=>selectSheet(i);tabs.append(b);
  });
  function selectSheet(i) {
    selected=i;expanded=new Set([sheets[i].rootTopic.id]);
    [...tabs.children].forEach((b,j)=>b.setAttribute('aria-selected',String(i===j)));
    panel.setAttribute('aria-labelledby','xmind-sheet-'+i);
    render(true);fit();showNotes(sheets[i].rootTopic);
    counter.textContent=counter.textContent.replace(/\d+% zoom$/,Math.round(zoom*100)+'% zoom');
  }
  const actions={
    expand:()=>{const add=n=>{if(children(n).length)expanded.add(n.id);children(n).forEach(add)};add(sheets[selected].rootTopic);render(true)},
    collapse:()=>{expanded=new Set([sheets[selected].rootTopic.id]);render(true);fit()},
    fit:()=>fit(),
    in:()=>{zoom=Math.min(2.5,zoom*1.25);updateSize(true)},
    out:()=>{zoom=Math.max(.35,zoom/1.25);updateSize(true)}
  };
  root.querySelectorAll('[data-xmind-action]').forEach(b=>b.onclick=()=>{actions[b.dataset.xmindAction]();counter.textContent=counter.textContent.replace(/\d+% zoom$/,Math.round(zoom*100)+'% zoom')});
  let drag=null;
  viewport.addEventListener('pointerdown',e=>{if(e.target.closest('.xmind-topic')||e.pointerType==='touch')return;drag={x:e.clientX,y:e.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};viewport.setPointerCapture(e.pointerId);viewport.classList.add('is-dragging')});
  viewport.addEventListener('pointermove',e=>{if(!drag)return;viewport.scrollLeft=drag.left-(e.clientX-drag.x);viewport.scrollTop=drag.top-(e.clientY-drag.y)});
  const end=()=>{drag=null;viewport.classList.remove('is-dragging')};viewport.addEventListener('pointerup',end);viewport.addEventListener('pointercancel',end);
  selectSheet(0);
})();
