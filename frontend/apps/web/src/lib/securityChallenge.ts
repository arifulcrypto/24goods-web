import { ref, shallowRef } from 'vue'
import { ApiError, solvePow, uuid, type ApiClient, type PowChallenge, type PowSolution } from '@24goods/shared'
export interface Challenge { id:string;action:'payment_initiation'|'site_visit';pow:PowChallenge;turnstile:{required:boolean;site_key:string;action:string};expires_at:number }
export const siteAccessBlocked=ref(false)
export const activeChallenge=shallowRef<Challenge|null>(null)
export const challengeSolving=ref(false)
export const challengeProgress=ref(0)
let resolveChallenge:((token:string|null)=>void)|null=null
let challengeController:AbortController|null=null
export function finishChallenge(token:string|null):void { resolveChallenge?.(token); resolveChallenge=null; activeChallenge.value=null }
export function cancelChallenge():void { challengeController?.abort(); finishChallenge(null) }
export async function completeChallenge(ch:Challenge):Promise<{challenge_id:string;pow:PowSolution;turnstile_token?:string}> {
  if(challengeSolving.value || activeChallenge.value) throw new ApiError('too_frequent','Verification already running',429)
  const ctrl=new AbortController();challengeController=ctrl;challengeSolving.value=true;challengeProgress.value=0
  try {
    const proof=await solvePow(ch.pow,{signal:ctrl.signal,onProgress:p=>challengeProgress.value=p})
    let token:string|undefined
    if(ch.turnstile.required){activeChallenge.value=ch;const answer=await new Promise<string|null>(resolve=>{resolveChallenge=resolve});if(answer===null)throw new ApiError('turnstile_failed','Verification cancelled',400);token=answer}
    return {challenge_id:ch.id,pow:proof,...(token?{turnstile_token:token}:{})}
  } finally {challengeSolving.value=false;challengeController=null}
}
export async function verifiedPay(client:ApiClient,target:'order'|'checkout',ref:string,channel?:string):Promise<{pay_url:string}> {
  // Policy is fetched at click time; the backend remains authoritative if it changes meanwhile.
  const site=await client.get<{security_challenges?:{payment_initiation:boolean}}>('/site')
  const idempotency_key=uuid()
  let proof={}
  if(site.security_challenges?.payment_initiation){const ch=await client.post<Challenge>('/security/challenges',{action:'payment_initiation',target,ref,channel});proof=await completeChallenge(ch)}
  return client.post<{pay_url:string}>(target==='order'?`/orders/${encodeURIComponent(ref)}/pay`:`/checkouts/${encodeURIComponent(ref)}/pay`,{channel,idempotency_key,...proof})
}
