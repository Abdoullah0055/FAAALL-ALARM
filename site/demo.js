const button = document.querySelector("#demo-action");
const status = document.querySelector("#demo-status");
const steps = [...document.querySelectorAll("[data-step]")];
const stages = [
  ["Simuler l’envoi du SMS", "Chute confirmée dans cet exemple. Le délai d’annulation est écoulé."],
  ["Simuler la réponse du proche", "SMS · Simulé"],
  ["Rejouer la simulation", "En route · Arrivée annoncée dans environ 20 min."],
];
let stage = 0;

function render() {
  button.textContent = stages[stage][0];
  status.textContent = stages[stage][1];
  steps.forEach((step, index) => {
    const ready = stage > index;
    step.dataset.ready = String(ready);
    step.querySelector(".bubble").hidden = !ready;
  });
}

button.hidden = false;
button.addEventListener("click", () => {
  stage = (stage + 1) % stages.length;
  render();
});
render();
