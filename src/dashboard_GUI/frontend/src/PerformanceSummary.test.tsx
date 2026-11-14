import { render,screen } from '@testing-library/react'
import { expect,it } from 'vitest'
import { PerformanceSummary,type PerformanceRecord } from './PerformanceSummary'

const record=(overrides:Partial<PerformanceRecord>={}):PerformanceRecord=>({
 strategy:'random',baseline:'nearest_astar',robots:10,metric:'order_makespan_s',n:2,expected_n:2,complete:true,
 baseline_mean:100,mean_difference:-5,ci_low:-8,ci_high:-2,percent_of_baseline:-5,...overrides,
})

it('keeps missing records and unavailable intervals visibly unknown',()=>{
 const {rerender}=render(<PerformanceSummary records={[]} complete method="paired" provenanceHref="#comparison-provenance"/>)
 expect(screen.getByText(/No eligible paired metric values/i)).toBeVisible()
 rerender(<PerformanceSummary records={[record({n:1,expected_n:1,ci_low:null,ci_high:null,percent_of_baseline:null})]} complete method="paired" provenanceHref="#comparison-provenance"/>)
 expect(screen.getByText('Unavailable (n < 2)')).toBeVisible()
 expect(screen.getByText('—')).toBeVisible()
})

it('labels metric direction without inferring a ranking',()=>{
 render(<PerformanceSummary records={[record(),record({metric:'throughput_orders_per_s',mean_difference:.02,baseline_mean:.1,percent_of_baseline:20})]} complete method="paired" provenanceHref="#comparison-provenance"/>)
 expect(screen.getByText('Lower is better')).toBeVisible()
 expect(screen.getByText('Higher is better')).toBeVisible()
 expect(screen.getByText(/No winner or ranking is inferred/i)).toBeVisible()
})

it('warns about incomplete samples and links to provenance',()=>{
 render(<PerformanceSummary records={[record({n:2,expected_n:4,complete:false})]} complete={false} method="paired 95% t interval" provenanceHref="#comparison-provenance"/>)
 expect(screen.getByRole('status')).toHaveTextContent('2 / 4')
 expect(screen.getByRole('link',{name:/provenance and method/i})).toHaveAttribute('href','#comparison-provenance')
})
