package body Apps.Templates.Views.Templates_Views is

   function Template_Index_SQL return String is
   begin
      return
        "SELECT id, app_name, template_name "
        & "FROM templates_template "
        & "ORDER BY app_name, template_name";
   end Template_Index_SQL;

end Apps.Templates.Views.Templates_Views;
