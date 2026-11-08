import {describe,expect,it,vi} from 'vitest'
vi.mock('../src/ui',()=>({toastError:vi.fn(),toastOk:vi.fn()}))
import {useRead} from '../src/composables/useRead'
import {toastOk} from '../src/ui'
describe('independent overview and event reads',()=>{
 it('runs both parallel reads and never displays a save notification',async()=>{
  const overview=useRead(),events=useRead()
  const results=await Promise.all([overview.run(async()=>({healthy:true})),events.run(async()=>['login'])])
  expect(results).toEqual([{healthy:true},['login']])
  expect(toastOk).not.toHaveBeenCalled()
 })
 it('marks failed refresh state without discarding the caller’s last sample',async()=>{
  const read=useRead()
  expect(await read.run(async()=>{throw new Error('synthetic')})).toBeUndefined()
  expect(read.failed.value).toBe(true)
  await read.run(async()=>({healthy:true}));expect(read.failed.value).toBe(false)
 })
})
