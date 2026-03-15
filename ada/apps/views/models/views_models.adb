with Arthexis.ORM;

package body Apps.Views.Models.Views_Models is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS views_view ("
         & "id INTEGER PRIMARY KEY, "
         & "app_name TEXT NOT NULL, "
         & "view_name TEXT NOT NULL, "
         & "UNIQUE(app_name, view_name)"
         & ");");
   end Install;

end Apps.Views.Models.Views_Models;
