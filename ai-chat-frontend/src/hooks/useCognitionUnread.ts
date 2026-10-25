import {useEffect,useState} from 'react';
import {cognitionRequest} from '../components/cognitionApi';

export function useCognitionUnread(){
  const [counts,setCounts]=useState<Record<string,number>>({});
  useEffect(()=>{
    let active=true;
    const refresh=()=>{cognitionRequest('/unread').then(data=>{if(active)setCounts(data.counts);}).catch(()=>{});};
    refresh(); const timer=window.setInterval(refresh,30000);
    window.addEventListener('cognition-read',refresh); window.addEventListener('focus',refresh);
    return()=>{active=false;window.clearInterval(timer);window.removeEventListener('cognition-read',refresh);window.removeEventListener('focus',refresh);};
  },[]);
  return counts;
}
