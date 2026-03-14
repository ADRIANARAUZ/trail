document.getElementById("search").addEventListener("keyup", function () {
  let v = this.value.toLowerCase();
  document.querySelectorAll("#tabla tr").forEach((r, i) => {
    if (i === 0) return;
    r.style.display = r.innerText.toLowerCase().includes(v) ? "" : "none";
  });
});

document.getElementById("filterPago").addEventListener("change", function () {
  let v = this.value;
  document.querySelectorAll("#tabla tr").forEach((r, i) => {
    if (i === 0) return;
    r.style.display = v === "" || r.innerText.includes(v) ? "" : "none";
  });
});
