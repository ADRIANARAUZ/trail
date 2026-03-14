
function showSection(id){
document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'))
document.querySelectorAll('.menu button').forEach(b=>b.classList.remove('active'))
document.getElementById(id).classList.add('active')
event.target.classList.add('active')
}
