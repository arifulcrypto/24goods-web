import {afterEach,describe,expect,it,vi} from 'vitest'
import {browserLocale,detectLocale,localePreference,persistLocale} from '../src/util'
import {imageQuality,lowData,previewImageUrl} from '../src/media/preferences'
afterEach(()=>{vi.unstubAllGlobals();imageQuality.value='auto'})
describe('browser language preference',()=>{
 it('uses ordered supported browser preferences without saving an inferred language',()=>{
  const values=new Map<string,string>()
  vi.stubGlobal('localStorage',{getItem:(k:string)=>values.get(k)??null,setItem:(k:string,v:string)=>values.set(k,v)})
  vi.stubGlobal('location',{search:''})
  vi.stubGlobal('navigator',{languages:['fr-FR','zh-Hant','en-US'],language:'en-US'})
  expect(browserLocale()).toBe('zh-CN');expect(detectLocale()).toBe('zh-CN');expect(values.size).toBe(0)
  persistLocale('en');expect(detectLocale()).toBe('en')
  persistLocale('auto');expect(localePreference()).toBe('auto');expect(detectLocale()).toBe('zh-CN')
  vi.stubGlobal('navigator',{languages:['en-GB','zh-CN'],language:'zh-CN'});expect(detectLocale()).toBe('en')
 })
 it('keeps URL overrides separate from explicit preferences and handles blocked storage',()=>{
  vi.stubGlobal('localStorage',{getItem:()=>{throw new Error('blocked')},setItem:()=>{throw new Error('blocked')}})
  vi.stubGlobal('location',{search:'?lang=zh-CN'});expect(detectLocale()).toBe('zh-CN');persistLocale('auto')
 })
})
describe('image data preferences',()=>{
 it('uses private thumbnails for unknown/cellular networks and supports explicit quality',()=>{
  vi.stubGlobal('navigator',{});expect(lowData()).toBe(true)
  expect(previewImageUrl('/api/v1/files/example')).toContain('variant=small')
  imageQuality.value='high';expect(previewImageUrl('/api/v1/files/example')).toBe('/api/v1/files/example')
  imageQuality.value='auto';vi.stubGlobal('navigator',{connection:{type:'wifi',effectiveType:'4g'}});expect(lowData()).toBe(false)
  vi.stubGlobal('navigator',{connection:{type:'cellular',effectiveType:'4g'}});expect(lowData()).toBe(true)
 })
})

describe('actual chat URL pipeline',()=>{
 it('selects a thumbnail after the asset URL is made absolute, without changing other origins',async()=>{
  const {safeAssetUrl}=await import('../src/api/endpoints')
  vi.stubGlobal('location',{href:'https://shop.example/chat'})
  imageQuality.value='low'
  const absolute=safeAssetUrl('/api/v1/files/example')
  expect(absolute).toBe('https://shop.example/api/v1/files/example')
  expect(previewImageUrl(absolute)).toBe('https://shop.example/api/v1/files/example?variant=small')
  expect(previewImageUrl('https://other.example/api/v1/files/example')).toBe('https://other.example/api/v1/files/example')
 })
})
