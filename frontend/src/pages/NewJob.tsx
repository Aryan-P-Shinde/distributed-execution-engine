import { SubmitJobForm } from '../components/SubmitJobForm';
import { jobPath } from '../hooks/useHashRoute';

export function NewJob() {
  return (
    <>
      <h1>Submit a job</h1>
      <SubmitJobForm onSubmitted={(job) => { window.location.hash = jobPath(job.id); }} />
    </>
  );
}
