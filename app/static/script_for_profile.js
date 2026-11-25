// My Profile page. The form now submits to the server (it used to save to localStorage only);
// this script just previews a newly chosen profile picture before it is uploaded.
document.addEventListener('DOMContentLoaded', () => {
    const profilePicInput = document.getElementById('profilePicInput');
    const profilePic = document.getElementById('profilePic');

    profilePicInput.addEventListener('change', () => {
        const file = profilePicInput.files[0];
        if (file) {
            const reader = new FileReader();
            reader.onload = () => {
                profilePic.src = reader.result;
            };
            reader.readAsDataURL(file);
        }
    });
});
