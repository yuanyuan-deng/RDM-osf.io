<%inherit file="notify_base.mako" />

<%def name="content()">
<tr>
  <td style="border-collapse: collapse;">
    Hello ${referrer.fullname},<br>
    <br>
    You recently added ${fullname} to "${node.title}". ${fullname} wants to claim their account, but the email address they provided is different from the one you provided.  To maintain security of your project, we are sending the account confirmation to you first.<br>
    <br>
    IMPORTANT: To ensure that the correct person is added to your project please forward the message below to ${fullname}.<br>
    <br>
    After ${fullname} confirms their account, they will be able to contribute to the project.<br>
    <br>
    <br>
    ----------------------<br>
    <br>
    <br>
    Hello ${fullname},<br>
    <br>
    You have been added by ${referrer.fullname} as a contributor to the project "${node.title}" on the GakuNin RDM. To claim yourself as a contributor to the project, visit this url:<br>
    <br>
    ${claim_url}<br>
    <br>
    Once you are logged in, you will be able to make contributions to ${node.title}.<br>
    <br>
    Sincerely,<br>
    <br>
    The GRDM Team<br>
    <br>
    Want more information? Visit https://rdm.nii.ac.jp/ or https://nii.ac.jp/ for information about the GakuNin RDM and its supporting organization, the National Institute of Informatics. Questions? Email ${osf_contact_email}<br>


</tr>
</%def>
