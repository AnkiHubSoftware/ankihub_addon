import { showTutorialModal } from "../lib/main";
import './style.css';


showTutorialModal({
  body: "Click here!",
  target: "#target",
  currentStep: 1,
  stepCount: 1,
});
