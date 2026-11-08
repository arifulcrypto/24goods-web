import {ref} from 'vue'
import {toastError} from '../ui'
/** A read request has its own state and never emits a save-success notification. */
export function useRead(){
  const busy=ref(false),failed=ref(false)
  async function run<T>(fetcher:()=>Promise<T>):Promise<T|undefined>{
    if(busy.value)return undefined
    busy.value=true
    try{const result=await fetcher();failed.value=false;return result}
    catch(error){failed.value=true;toastError(error);return undefined}
    finally{busy.value=false}
  }
  return{busy,failed,run}
}
