import CognitionPage from '../pages/CognitionPage';
export { cognitionRequest } from './cognitionApi';

// Compatibility for isolated verification pages and older callers.
export default function CognitionPanel({ initialDomain = 'self', onClose }: {
  initialDomain?: 'self' | 'world'; onClose: () => void;
}) {
  return <CognitionPage initialBook={initialDomain} onBack={onClose} />;
}
